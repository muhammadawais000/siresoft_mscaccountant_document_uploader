// Modal dialogs built on <dialog>: focus is trapped by the browser and returned after.
import { h } from "./dom.js";

/**
 * @typedef {{
 *   title: string,
 *   body?: string,
 *   confirmLabel?: string,
 *   danger?: boolean,
 *   input?: { label: string, required?: boolean, multiline?: boolean, maxLength?: number },
 *   checkbox?: { label: string, required?: boolean },
 * }} DialogOptions
 * @typedef {{ value: string, checked: boolean }} DialogResult
 */

/**
 * Show a confirm dialog. Resolves with the entered values, or null if cancelled.
 * @param {DialogOptions} opts
 * @returns {Promise<DialogResult | null>}
 */
export function openDialog(opts) {
  const opener = /** @type {HTMLElement | null} */ (document.activeElement);
  const titleId = `dlg-${Math.random().toString(36).slice(2)}`;

  /** @type {HTMLInputElement | HTMLTextAreaElement | null} */
  let field = null;
  if (opts.input) {
    const attrs = {
      class: opts.input.multiline ? "textarea" : "input",
      id: `${titleId}-input`,
      maxlength: String(opts.input.maxLength ?? 500),
      required: opts.input.required ?? false,
    };
    field = /** @type {HTMLInputElement | HTMLTextAreaElement} */ (
      h(opts.input.multiline ? "textarea" : "input", attrs)
    );
  }
  /** @type {HTMLInputElement | null} */
  const check = opts.checkbox
    ? /** @type {HTMLInputElement} */ (h("input", { type: "checkbox", id: `${titleId}-check` }))
    : null;

  const confirm = /** @type {HTMLButtonElement} */ (
    h("button", { class: `btn ${opts.danger ? "btn--danger" : "btn--primary"}`, type: "submit" },
      opts.confirmLabel ?? "Confirm")
  );
  const form = h("form", { method: "dialog" },
    h("div", { class: "dialog__body" },
      h("h2", { class: "dialog__title", id: titleId }, opts.title),
      opts.body ? h("p", {}, opts.body) : null,
      field && opts.input
        ? h("div", { class: "field" },
          h("label", { class: "field__label", for: field.id }, opts.input.label), field)
        : null,
      check && opts.checkbox
        ? h("label", { class: "check", for: check.id }, check, opts.checkbox.label)
        : null,
    ),
    h("div", { class: "dialog__actions" },
      h("button", { class: "btn", type: "button", value: "cancel", "data-cancel": true }, "Cancel"),
      confirm,
    ),
  );
  const dialog = /** @type {HTMLDialogElement} */ (
    h("dialog", { class: "dialog", "aria-labelledby": titleId }, form)
  );

  const sync = () => {
    const missingText = Boolean(opts.input?.required && field && !field.value.trim());
    const missingCheck = Boolean(opts.checkbox?.required && check && !check.checked);
    confirm.disabled = missingText || missingCheck;
  };
  field?.addEventListener("input", sync);
  check?.addEventListener("change", sync);
  sync();

  document.body.append(dialog);
  dialog.showModal();
  (field ?? confirm).focus();

  return new Promise((resolve) => {
    /** @type {DialogResult | null} */
    let result = null;
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (confirm.disabled) return;
      result = { value: field?.value.trim() ?? "", checked: check?.checked ?? false };
      dialog.close();
    });
    form.querySelector("[data-cancel]")?.addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      dialog.remove();
      opener?.focus();
      resolve(result);
    });
  });
}
