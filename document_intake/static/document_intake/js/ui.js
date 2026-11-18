// Small behaviours shared by every page. No HTML is built here: the markup comes from
// the server templates and this file only toggles classes and text.
(() => {
  // 1. hx-confirm opens our styled <dialog> (base.html) instead of window.confirm.
  const dialog = document.getElementById("confirm-dialog");
  if (dialog && typeof dialog.showModal === "function") {
    const text = document.getElementById("confirm-text");
    const ok = document.getElementById("confirm-ok");
    document.body.addEventListener("htmx:confirm", (event) => {
      if (!event.detail.question) return;
      event.preventDefault();
      const source = event.detail.elt;
      const action = source.dataset.confirmAction || "OK";
      text.textContent = event.detail.question;
      ok.textContent = action;
      ok.classList.toggle("btn--danger-solid", action === "Delete");
      ok.classList.toggle("btn--primary", action !== "Delete");
      dialog.returnValue = "";
      dialog.addEventListener(
        "close",
        () => {
          if (dialog.returnValue === "ok") event.detail.issueRequest(true);
        },
        { once: true },
      );
      dialog.showModal();
    });
    // Click on the dimmed backdrop = Cancel.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close("cancel");
    });
  }

  // 2. Messages: a close button, and success messages fade away on their own.
  const dismiss = (notice) => {
    notice.classList.add("is-leaving");
    notice.addEventListener("animationend", () => notice.remove(), { once: true });
  };
  document.body.addEventListener("click", (event) => {
    const button = event.target.closest("[data-dismiss]");
    if (button) dismiss(button.closest(".notice"));
  });
  document.body.addEventListener("htmx:afterSwap", (event) => {
    if (event.detail.target.id !== "messages") return;
    event.detail.target.querySelectorAll("[data-autoclose]").forEach((notice) => {
      setTimeout(() => notice.isConnected && dismiss(notice), 5000);
    });
  });

  // 3. A row being deleted fades out while the request runs; it comes back if it fails.
  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const elt = event.detail.elt;
    if (elt.hasAttribute("data-removes-row")) {
      elt.closest("tr")?.classList.add("is-removing");
    }
  });
  document.body.addEventListener("htmx:afterRequest", (event) => {
    const elt = event.detail.elt;
    if (!elt.hasAttribute("data-removes-row")) return;
    const row = elt.closest("tr");
    if (!row) return;
    // Success answers carry "HX-Trigger: documents-changed"; an error message does not.
    const deleted = event.detail.successful && event.detail.xhr.getResponseHeader("HX-Trigger");
    row.classList.toggle("is-removing", Boolean(deleted));
    row.classList.toggle("is-removed", Boolean(deleted));
  });
})();
