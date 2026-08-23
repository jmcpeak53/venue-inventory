"use strict";

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-target]");
  if (!button) return;

  const target = document.getElementById(button.dataset.copyTarget);
  if (!target || !navigator.clipboard) {
    button.textContent = "Select and copy the code";
    return;
  }

  try {
    await navigator.clipboard.writeText(target.textContent.trim());
    const original = button.textContent;
    button.textContent = "Copied";
    window.setTimeout(() => {
      button.textContent = original;
    }, 1800);
  } catch (_error) {
    button.textContent = "Select and copy the code";
  }
});
