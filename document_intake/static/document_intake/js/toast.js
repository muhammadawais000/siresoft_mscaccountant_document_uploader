// Toast notifications in a polite live region. Errors stay until dismissed.
import { h } from "./dom.js";

/** @typedef {"ok" | "warn" | "err" | "info"} ToastTone */

/** @returns {HTMLElement} */
function region() {
  let el = document.getElementById("toasts");
  if (!el) {
    el = h("div", { id: "toasts", class: "toasts", "aria-live": "polite", role: "status" });
    document.body.append(el);
  }
  return el;
}

/**
 * Show a toast.
 * @param {string} message
 * @param {{ tone?: ToastTone, timeout?: number }} [opts]
 */
export function toast(message, opts = {}) {
  const tone = opts.tone ?? "info";
  const timeout = opts.timeout ?? (tone === "err" ? 0 : 5000);
  const close = () => item.remove();
  const item = h(
    "div",
    { class: `toast toast--${tone}`, role: tone === "err" ? "alert" : null },
    h("div", { class: "toast__text" }, message),
    h("button", { class: "toast__close", type: "button", "aria-label": "Dismiss", onclick: close }, "×"),
  );
  region().append(item);
  if (timeout > 0) setTimeout(close, timeout);
}

/**
 * Announce text to screen readers without showing a toast.
 * @param {string} message
 */
export function announce(message) {
  const el = document.getElementById("sr-status");
  if (el) {
    el.textContent = "";
    // A fresh text node is announced even if the message repeats.
    requestAnimationFrame(() => {
      el.textContent = message;
    });
  }
}
