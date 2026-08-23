(() => {
  "use strict";

  const root = document.querySelector("[data-basket-root]");
  if (!root) return;

  const csrf = document.querySelector("#basket-csrf")?.value || "";
  const itemTypes = document.querySelector("#basket-item-types");
  const units = document.querySelector("#basket-units");
  const basketOnly = root.dataset.basketOnly === "true";
  const cards = new Map();
  const queue = [];
  let active = null;

  root.querySelectorAll("[data-basket-card]").forEach((card) => {
    const input = card.querySelector("[data-quantity-input]");
    const status = card.querySelector("[data-save-status]");
    const retry = card.querySelector("[data-retry]");
    if (!input || !status || !retry) return;

    const state = {
      id: card.dataset.itemId,
      card,
      input,
      status,
      retry,
      timer: null,
      pending: null,
      queued: false,
      lastAttempt: null,
      serverValue: Number(card.dataset.selected || 0),
    };
    cards.set(state.id, state);

    input.addEventListener("input", () => schedule(state));
    input.addEventListener("change", () => schedule(state, true));
    retry.addEventListener("click", () => retrySave(state));
  });

  function parsedQuantity(input) {
    const value = input.value.trim();
    if (!/^\d+$/.test(value)) return null;
    const quantity = Number(value);
    const maximum = Number(input.max);
    if (!Number.isSafeInteger(quantity) || quantity < 0 || quantity > maximum) {
      return null;
    }
    return quantity;
  }

  function schedule(state, immediate = false) {
    window.clearTimeout(state.timer);
    const quantity = parsedQuantity(state.input);
    if (quantity === null) {
      state.pending = null;
      state.input.setAttribute("aria-invalid", "true");
      showError(state, `Enter a whole number from 0 through ${state.input.max}.`);
      return;
    }

    state.input.removeAttribute("aria-invalid");
    state.pending = quantity;
    state.lastAttempt = quantity;
    showSaving(state);
    if (immediate) {
      enqueue(state);
    } else {
      state.timer = window.setTimeout(() => enqueue(state), 300);
    }
  }

  function enqueue(state) {
    if (!state.queued) {
      state.queued = true;
      queue.push(state);
    }
    drain();
  }

  async function drain() {
    if (active !== null) return;
    const state = queue.shift();
    if (!state) return;
    state.queued = false;
    if (state.pending === null) {
      drain();
      return;
    }

    active = state;
    const sentQuantity = state.pending;
    state.pending = null;
    state.lastAttempt = sentQuantity;
    showSaving(state);

    const body = new URLSearchParams({
      csrf_token: csrf,
      quantity: String(sentQuantity),
      revision: root.dataset.revision,
    });

    try {
      const response = await fetch(state.card.dataset.saveUrl, {
        method: "POST",
        body,
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }

      if (response.ok && payload?.ok) {
        handleSaved(state, payload);
      } else if (response.status === 409 && payload?.code === "stale_revision") {
        handleStale(state, sentQuantity, payload);
      } else if (
        response.status === 422 &&
        payload?.code === "quantity_out_of_range"
      ) {
        handleStockChange(state, payload);
      } else if (response.status === 404 && payload?.code === "item_not_found") {
        state.pending = null;
        applySnapshot(payload);
        state.input.disabled = true;
        showError(state, payload.message);
      } else {
        handleRetryableFailure(state, sentQuantity, payload);
      }
    } catch (_error) {
      handleRetryableFailure(state, sentQuantity, null);
    } finally {
      active = null;
      drain();
    }
  }

  function handleSaved(state, payload) {
    const inputInvalidSinceSend = parsedQuantity(state.input) === null;
    const preserve = pendingIds();
    if (inputInvalidSinceSend) preserve.add(state.id);
    applySnapshot(payload, preserve);
    // Keep the in-progress invalid draft and its error; this response is stale.
    if (inputInvalidSinceSend) {
      return;
    }
    const persisted = Number(payload.selections?.[state.id]?.quantity ?? 0);
    if (state.pending !== null && state.pending !== persisted) {
      showSaving(state);
      enqueue(state);
      return;
    }
    if (state.pending === persisted) state.pending = null;
    state.input.value = String(persisted);
    state.lastAttempt = null;
    showSaved(state);
    if (basketOnly && persisted === 0) {
      const hadFocus = state.card.contains(document.activeElement);
      const nextInput = state.card.nextElementSibling?.querySelector(
        "[data-quantity-input]",
      );
      const previousInput = state.card.previousElementSibling?.querySelector(
        "[data-quantity-input]",
      );
      const grid = state.card.parentElement;
      cards.delete(state.id);
      state.card.remove();
      if (grid && cards.size === 0) {
        const empty = document.createElement("p");
        empty.className = "empty-state basket-empty";
        empty.textContent = "Your basket has no items matching this search.";
        grid.replaceWith(empty);
      }
      if (hadFocus) {
        const focusTarget =
          nextInput ||
          previousInput ||
          root.querySelector('.basket-toggle [aria-current="page"]');
        focusTarget?.focus();
      }
    }
  }

  function handleStale(state, sentQuantity, payload) {
    const desired = state.pending ?? sentQuantity;
    state.pending = null;
    applySnapshot(payload, pendingIds());
    state.lastAttempt = desired;
    showError(state, payload.message, true);
  }

  function handleStockChange(state, payload) {
    const pending = state.pending;
    applySnapshot(payload, pendingIds());
    if (pending !== null && parsedQuantity(state.input) !== null) {
      showSaving(state);
      enqueue(state);
      return;
    }
    state.pending = null;
    state.lastAttempt = null;
    state.input.value = String(state.serverValue);
    state.input.setAttribute("aria-invalid", "true");
    showError(state, payload.message);
  }

  function handleRetryableFailure(state, sentQuantity, payload) {
    const desired = state.pending ?? sentQuantity;
    state.pending = null;
    if (payload) applySnapshot(payload, new Set([state.id, ...pendingIds()]));
    state.lastAttempt = desired;
    showError(
      state,
      payload?.message || "The change could not be saved. Retry.",
      true,
    );
  }

  function retrySave(state) {
    if (state.lastAttempt === null) return;
    state.input.disabled = false;
    state.input.value = String(state.lastAttempt);
    state.input.removeAttribute("aria-invalid");
    state.pending = state.lastAttempt;
    showSaving(state);
    enqueue(state);
  }

  function pendingIds() {
    return new Set(
      [...cards.values()]
        .filter((state) => state.pending !== null)
        .map((state) => state.id),
    );
  }

  function applySnapshot(payload, preserve = new Set()) {
    if (Number.isInteger(payload?.revision)) {
      root.dataset.revision = String(payload.revision);
    }
    if (payload?.totals) {
      if (itemTypes) itemTypes.textContent = String(payload.totals.item_types);
      if (units) units.textContent = String(payload.totals.units);
    }
    Object.entries(payload?.selections || {}).forEach(([id, selection]) => {
      const state = cards.get(id);
      if (!state) return;
      state.serverValue = Number(selection.quantity);
      state.card.dataset.selected = String(selection.quantity);
      state.card.dataset.stock = String(selection.stock_quantity);
      state.input.max = String(selection.stock_quantity);
      const stockValue = state.card.querySelector("[data-stock-value]");
      const remaining = state.card.querySelector("[data-remaining]");
      if (stockValue) stockValue.textContent = String(selection.stock_quantity);
      if (remaining) remaining.textContent = String(selection.remaining_quantity);
      if (!preserve.has(id)) state.input.value = String(selection.quantity);
    });
  }

  function showSaving(state) {
    state.status.className = "save-status saving";
    state.status.setAttribute("role", "status");
    state.status.setAttribute("aria-live", "polite");
    state.status.textContent = "Saving";
    state.retry.hidden = true;
  }

  function showSaved(state) {
    state.status.className = "save-status saved";
    state.status.setAttribute("role", "status");
    state.status.setAttribute("aria-live", "polite");
    state.status.textContent = "Saved";
    state.retry.hidden = true;
  }

  function showError(state, message, retryable = false) {
    state.status.className = "save-status save-error";
    state.status.setAttribute("role", "alert");
    state.status.setAttribute("aria-live", "assertive");
    state.status.textContent = message;
    state.retry.hidden = !retryable;
  }
})();
