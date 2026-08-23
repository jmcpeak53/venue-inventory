import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

const [baseUrl, adminPassword, bookingIdRaw] = process.argv.slice(2);
const bookingId = Number(bookingIdRaw);
if (!baseUrl || !adminPassword || !Number.isInteger(bookingId)) {
  throw new Error(
    "Usage: admin_booking_smoke.mjs <base-url> <admin-password> <booking-id>",
  );
}

const chromeExecutable = process.env.CHROME_EXECUTABLE || "google-chrome";
const profile = await mkdtemp(join(tmpdir(), "venue-inventory-admin-chrome-"));
const chrome = spawn(
  chromeExecutable,
  [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-sync",
    "--metrics-recording-only",
    "--no-first-run",
    "--remote-allow-origins=*",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

let socket;
try {
  const websocketUrl = await devtoolsUrl(chrome);
  socket = new WebSocket(websocketUrl);
  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", reject, { once: true });
  });

  const cdp = createCdpClient(socket);
  const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await cdp.send("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Runtime.enable", {}, sessionId);
  const exceptions = [];
  cdp.on("Runtime.exceptionThrown", (message) => {
    if (message.sessionId === sessionId) exceptions.push(message.params);
  });

  async function evaluate(expression) {
    const result = await cdp.send(
      "Runtime.evaluate",
      { expression, returnByValue: true, awaitPromise: true },
      sessionId,
    );
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
    }
    return result.result.value;
  }

  async function navigate(url) {
    const loaded = cdp.waitFor("Page.loadEventFired", sessionId);
    await cdp.send("Page.navigate", { url }, sessionId);
    await loaded;
  }

  async function waitFor(expression, message) {
    const deadline = Date.now() + 8_000;
    while (Date.now() < deadline) {
      if (await evaluate(expression)) return;
      await delay(50);
    }
    throw new Error(message);
  }

  await navigate(`${baseUrl}/admin/login`);
  const loginLoaded = cdp.waitFor("Page.loadEventFired", sessionId);
  await evaluate(`(() => {
    const input = document.querySelector('#password');
    input.value = ${JSON.stringify(adminPassword)};
    input.form.requestSubmit();
    return true;
  })()`);
  await loginLoaded;
  await waitFor(
    `Boolean(document.querySelector('a[href="/admin/bookings"]'))`,
    "Administrator dashboard did not load after sign-in",
  );

  await navigate(`${baseUrl}/admin/bookings/${bookingId}`);
  await waitFor(
    `Boolean(document.querySelector('[data-booking-detail]'))`,
    "Booking detail did not load",
  );

  const quantityLoaded = cdp.waitFor("Page.loadEventFired", sessionId);
  await evaluate(`(() => {
    const input = document.querySelector('#admin-quantity-1');
    input.value = '4';
    input.form.requestSubmit();
    return true;
  })()`);
  await quantityLoaded;
  await waitFor(
    `document.querySelector('#admin-quantity-1')?.value === '4'`,
    "Administrator quantity edit did not persist",
  );

  const preparation = await evaluate(`(() => {
    const quantity = document.querySelector('[data-preparation-quantity]')?.textContent;
    const remaining = document.querySelector('[data-preparation-remaining]')?.textContent;
    const warning = document.querySelector('[data-preparation-warning="negative"]');
    return {
      quantity: Number(quantity),
      remaining: Number(remaining),
      warning: Boolean(warning),
      reference: document.querySelector('[data-preparation-reference]')?.textContent,
      eventDate: document.querySelector('[data-preparation-event-date]')?.textContent,
    };
  })()`);
  assert(preparation.quantity === 4);
  assert(preparation.remaining === 1);
  assert(preparation.warning === false);
  assert(preparation.reference === "B-0001");
  assert(Boolean(preparation.eventDate));

  await cdp.send("Emulation.setEmulatedMedia", { media: "print" }, sessionId);
  const printState = await evaluate(`(() => {
    const header = document.querySelector('.site-header');
    const printButton = document.querySelector('[data-print-list]');
    const basketControls = document.querySelector('.admin-selection-list');
    const prep = document.querySelector('[data-preparation-list]');
    const item = document.querySelector('[data-preparation-item]');
    const quantity = document.querySelector('[data-preparation-quantity]');
    const reference = document.querySelector('[data-preparation-reference]');
    const eventDate = document.querySelector('[data-preparation-event-date]');
    function visible(element) {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    }
    return {
      headerHidden: !visible(header),
      printButtonHidden: !visible(printButton),
      basketHidden: !visible(basketControls),
      prepVisible: visible(prep),
      itemVisible: visible(item),
      quantityVisible: visible(quantity),
      referenceVisible: visible(reference),
      eventDateVisible: visible(eventDate),
      quantityText: quantity?.textContent,
    };
  })()`);
  assert(printState.headerHidden);
  assert(printState.printButtonHidden);
  assert(printState.basketHidden);
  assert(printState.prepVisible);
  assert(printState.itemVisible);
  assert(printState.quantityVisible);
  assert(printState.referenceVisible);
  assert(printState.eventDateVisible);
  assert(printState.quantityText === "4");
  assert(exceptions.length === 0);

  process.stdout.write(
    JSON.stringify({
      outcome: "passed",
      quantity: preparation.quantity,
      print_visible: printState.prepVisible && printState.itemVisible,
      controls_hidden:
        printState.headerHidden &&
        printState.printButtonHidden &&
        printState.basketHidden,
    }),
  );
} finally {
  if (socket?.readyState === WebSocket.OPEN) socket.close();
  if (!chrome.killed) chrome.kill("SIGTERM");
  await Promise.race([
    new Promise((resolve) => chrome.once("exit", resolve)),
    delay(2_000),
  ]);
  await rm(profile, { recursive: true, force: true }).catch(() => {});
}

function assert(condition) {
  if (!condition) throw new Error("Admin booking smoke assertion failed");
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function devtoolsUrl(process) {
  return new Promise((resolve, reject) => {
    let buffer = "";
    const timeout = setTimeout(
      () => reject(new Error("Chrome did not expose a DevTools endpoint")),
      8_000,
    );
    process.stderr.setEncoding("utf8");
    process.stderr.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(timeout);
        resolve(match[1]);
      }
    });
    process.once("exit", (code) => {
      clearTimeout(timeout);
      reject(new Error(`Chrome exited before startup (${code})`));
    });
  });
}

function createCdpClient(socket) {
  let nextId = 1;
  const pending = new Map();
  const listeners = new Map();

  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id);
      if (message.error) waiter.reject(new Error(message.error.message));
      else waiter.resolve(message.result || {});
      return;
    }
    for (const listener of listeners.get(message.method) || []) listener(message);
  });

  return {
    send(method, params = {}, sessionId = undefined) {
      const id = nextId++;
      const message = { id, method, params };
      if (sessionId) message.sessionId = sessionId;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        socket.send(JSON.stringify(message));
      });
    },
    on(method, listener) {
      const registered = listeners.get(method) || [];
      registered.push(listener);
      listeners.set(method, registered);
    },
    waitFor(method, sessionId, timeoutMilliseconds = 8_000) {
      return new Promise((resolve, reject) => {
        const registered = listeners.get(method) || [];
        const timeout = setTimeout(() => {
          const index = registered.indexOf(listener);
          if (index >= 0) registered.splice(index, 1);
          reject(new Error(`Timed out waiting for ${method}`));
        }, timeoutMilliseconds);
        function listener(message) {
          if (sessionId && message.sessionId !== sessionId) return;
          clearTimeout(timeout);
          const index = registered.indexOf(listener);
          if (index >= 0) registered.splice(index, 1);
          resolve(message.params || {});
        }
        registered.push(listener);
        listeners.set(method, registered);
      });
    },
  };
}
