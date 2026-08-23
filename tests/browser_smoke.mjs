import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawn } from "node:child_process";

const [baseUrl, accessCode, itemIdRaw] = process.argv.slice(2);
const itemId = Number(itemIdRaw);
if (!baseUrl || !accessCode || !Number.isInteger(itemId)) {
  throw new Error("Usage: browser_smoke.mjs <base-url> <access-code> <item-id>");
}

const chromeExecutable = process.env.CHROME_EXECUTABLE || "google-chrome";
const profile = await mkdtemp(join(tmpdir(), "venue-inventory-chrome-"));
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

  await navigate(`${baseUrl}/customer/login`);
  const loginLoaded = cdp.waitFor("Page.loadEventFired", sessionId);
  await evaluate(`(() => {
    const input = document.querySelector('#access_code');
    input.value = ${JSON.stringify(accessCode)};
    input.form.requestSubmit();
    return true;
  })()`);
  await loginLoaded;
  await waitFor(
    `Boolean(document.querySelector('[data-basket-root]'))`,
    "Customer basket did not load after sign-in",
  );

  const initial = await evaluate(`(() => {
    const card = document.querySelector('[data-item-id="${itemId}"]');
    const input = card?.querySelector('[data-quantity-input]');
    return {
      card: Boolean(card),
      value: input?.value,
      label: Boolean(document.querySelector('label[for="quantity-${itemId}"]')),
      status: card?.querySelector('[data-save-status]')?.textContent,
    };
  })()`);
  assert(initial.card && initial.value === "0" && initial.label);
  assert(initial.status === "Saved");

  await evaluate(`(() => {
    const input = document.querySelector('#quantity-${itemId}');
    input.value = '1';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
  })()`);
  await waitFor(
    `(() => {
      const card = document.querySelector('[data-item-id="${itemId}"]');
      return card.querySelector('[data-save-status]').getAttribute('role') === 'alert'
        && !card.querySelector('[data-retry]').hidden;
    })()`,
    "Transient failure did not expose an announced retry state",
  );
  await evaluate(`document.querySelector('[data-item-id="${itemId}"] [data-retry]').click()`);
  await waitFor(
    `(() => {
      const card = document.querySelector('[data-item-id="${itemId}"]');
      return card.querySelector('[data-save-status]').textContent === 'Saved'
        && card.querySelector('[data-quantity-input]').value === '1';
    })()`,
    "Retry did not save the intended quantity",
  );

  const rapidState = await evaluate(`(() => {
    const input = document.querySelector('#quantity-${itemId}');
    input.value = '2';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.value = '3';
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return document.querySelector('[data-item-id="${itemId}"] [data-save-status]').textContent;
  })()`);
  assert(rapidState === "Saving");
  await waitFor(
    `(() => {
      const card = document.querySelector('[data-item-id="${itemId}"]');
      return card.querySelector('[data-save-status]').textContent === 'Saved'
        && card.querySelector('[data-quantity-input]').value === '3'
        && document.querySelector('#basket-units').textContent === '3';
    })()`,
    "Rapid changes did not settle on the newest quantity",
  );

  await evaluate(`(() => {
    const originalFetch = window.fetch.bind(window);
    let staleOnce = true;
    window.fetch = async function (resource, init) {
      const url = String(resource);
      if (staleOnce && url.includes("/customer/selections/")) {
        staleOnce = false;
        const revision = Number(
          document.querySelector("[data-basket-root]").dataset.revision,
        );
        return new Response(
          JSON.stringify({
            ok: false,
            code: "stale_revision",
            message:
              "This basket changed elsewhere. Current values were refreshed; review them and retry your change.",
            revision,
            selections: {
              "${itemId}": {
                quantity: 9,
                stock_quantity: 5,
                remaining_quantity: -4,
              },
            },
            totals: { item_types: 1, units: 9 },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        );
      }
      return originalFetch(resource, init);
    };
    const input = document.querySelector("#quantity-${itemId}");
    input.value = "4";
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.value = "5";
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`);
  await waitFor(
    `(() => {
      const card = document.querySelector('[data-item-id="${itemId}"]');
      return card.querySelector('[data-save-status]').getAttribute('role') === 'alert'
        && !card.querySelector('[data-retry]').hidden
        && card.querySelector('[data-quantity-input]').value === '5'
        && card.querySelector('[data-remaining]').textContent === '-4'
        && !card.querySelector('[data-remaining-warning]').hidden;
    })()`,
    "Stale revision overwrote the newer in-flight quantity draft",
  );
  await evaluate(`document.querySelector('[data-item-id="${itemId}"] [data-retry]').click()`);
  await waitFor(
    `(() => {
      const card = document.querySelector('[data-item-id="${itemId}"]');
      return card.querySelector('[data-save-status]').textContent === 'Saved'
        && card.querySelector('[data-quantity-input]').value === '5'
        && card.querySelector('[data-remaining]').textContent === '0'
        && card.querySelector('[data-remaining-warning]').hidden
        && document.querySelector('#basket-units').textContent === '5';
    })()`,
    "Retry after a stale revision did not save the preserved draft",
  );

  await navigate(`${baseUrl}/customer/portal?view=basket`);
  const persisted = await evaluate(`(() => ({
    value: document.querySelector('#quantity-${itemId}')?.value,
    types: document.querySelector('#basket-item-types')?.textContent,
    units: document.querySelector('#basket-units')?.textContent,
    currentView: document.querySelector('.basket-toggle [aria-current="page"]')?.textContent,
  }))()`);
  assert(
    persisted.value === "5" &&
      persisted.types === "1" &&
      persisted.units === "5" &&
      persisted.currentView.trim() === "My basket",
  );

  const hidden = await evaluate(`(async () => {
    const csrf = document.querySelector('#basket-csrf').value;
    const response = await fetch('/_test/items/${itemId}/toggle-visibility', {
      method: 'POST',
      body: new URLSearchParams({ csrf_token: csrf }),
      credentials: 'same-origin',
    });
    return response.json();
  })()`);
  assert(hidden.is_visible === false);

  await navigate(`${baseUrl}/customer/portal`);
  const hiddenFromBrowsing = await evaluate(
    `!document.querySelector('[data-item-id="${itemId}"]')`,
  );
  assert(hiddenFromBrowsing);

  await navigate(`${baseUrl}/customer/portal?view=basket`);
  const locked = await evaluate(`(() => {
    const card = document.querySelector('[data-item-id="${itemId}"]');
    return {
      card: Boolean(card),
      unavailable: !card?.querySelector('[data-unavailable-badge]')?.hidden,
      disabled: card?.querySelector('[data-quantity-input]')?.disabled,
      value: card?.querySelector('[data-quantity-input]')?.value,
    };
  })()`);
  assert(
    locked.card && locked.unavailable && locked.disabled && locked.value === "5",
  );

  const craftedHiddenWrite = await evaluate(`(async () => {
    const root = document.querySelector('[data-basket-root]');
    const csrf = document.querySelector('#basket-csrf').value;
    const card = document.querySelector('[data-item-id="${itemId}"]');
    const response = await fetch(card.dataset.saveUrl, {
      method: 'POST',
      body: new URLSearchParams({
        csrf_token: csrf,
        quantity: '4',
        revision: root.dataset.revision,
      }),
      headers: { Accept: 'application/json' },
      credentials: 'same-origin',
    });
    const payload = await response.json();
    return {
      status: response.status,
      code: payload.code,
      quantity: payload.selections['${itemId}'].quantity,
    };
  })()`);
  assert(
    craftedHiddenWrite.status === 409 &&
      craftedHiddenWrite.code === "item_unavailable" &&
      craftedHiddenWrite.quantity === 5,
  );

  const shown = await evaluate(`(async () => {
    const csrf = document.querySelector('#basket-csrf').value;
    const response = await fetch('/_test/items/${itemId}/toggle-visibility', {
      method: 'POST',
      body: new URLSearchParams({ csrf_token: csrf }),
      credentials: 'same-origin',
    });
    return response.json();
  })()`);
  assert(shown.is_visible === true);
  await navigate(`${baseUrl}/customer/portal`);
  const restored = await evaluate(`(() => {
    const card = document.querySelector('[data-item-id="${itemId}"]');
    return {
      card: Boolean(card),
      unavailable: !card?.querySelector('[data-unavailable-badge]')?.hidden,
      disabled: card?.querySelector('[data-quantity-input]')?.disabled,
      value: card?.querySelector('[data-quantity-input]')?.value,
    };
  })()`);
  assert(
    restored.card &&
      !restored.unavailable &&
      !restored.disabled &&
      restored.value === "5",
  );

  await cdp.send(
    "Emulation.setDeviceMetricsOverride",
    { width: 375, height: 812, deviceScaleFactor: 1, mobile: true },
    sessionId,
  );
  const mobileLoaded = cdp.waitFor("Page.loadEventFired", sessionId);
  await cdp.send("Page.reload", { ignoreCache: true }, sessionId);
  await mobileLoaded;
  const mobile = await evaluate(`(() => {
    const input = document.querySelector('#quantity-${itemId}');
    const toggle = document.querySelector('.basket-toggle');
    return {
      noOverflow: document.documentElement.scrollWidth <= window.innerWidth,
      inputVisible: input.getBoundingClientRect().width > 0,
      toggleVisible: toggle.getBoundingClientRect().width > 0,
    };
  })()`);
  assert(mobile.noOverflow && mobile.inputVisible && mobile.toggleVisible);

  await cdp.send(
    "Emulation.setDeviceMetricsOverride",
    { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false },
    sessionId,
  );
  const desktopLoaded = cdp.waitFor("Page.loadEventFired", sessionId);
  await cdp.send("Page.reload", { ignoreCache: true }, sessionId);
  await desktopLoaded;
  const desktop = await evaluate(`(() => ({
    noOverflow: document.documentElement.scrollWidth <= window.innerWidth,
    cardWidth: document.querySelector('[data-basket-card]').getBoundingClientRect().width,
  }))()`);
  assert(desktop.noOverflow && desktop.cardWidth > 250);
  assert(exceptions.length === 0);

  process.stdout.write(
    JSON.stringify({
      outcome: "passed",
      rapid_quantity: 3,
      retry_visible: true,
      stale_draft_preserved: true,
      hidden_locking: true,
      mobile,
      desktop,
    }),
  );
} finally {
  if (socket?.readyState === WebSocket.OPEN) socket.close();
  if (!chrome.killed) chrome.kill("SIGTERM");
  await rm(profile, { recursive: true, force: true });
}

function assert(condition) {
  if (!condition) throw new Error("Browser smoke assertion failed");
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
