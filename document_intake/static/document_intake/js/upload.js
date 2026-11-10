// Upload panel: drag-and-drop, file picker, camera capture, per-file progress.
import { uploadFile, ApiError } from "./api.js";
import { h, replace } from "./dom.js";
import { formatBytes } from "./format.js";
import { toast } from "./toast.js";

/**
 * @typedef {{ maxUploadBytes: number, allowedExtensions: string[] }} UploadLimits
 * @typedef {{ id: string, original_filename: string, status: string, duplicate_of: string | null }} UploadedDoc
 */

/**
 * Client-side pre-check for fast feedback. The server re-validates everything
 * (extension, magic bytes, size), so this is a convenience, not a control.
 * @param {File} file
 * @param {UploadLimits} limits
 * @returns {string | null} error message, or null when acceptable
 */
export function precheck(file, limits) {
  const dot = file.name.lastIndexOf(".");
  const ext = dot >= 0 ? file.name.slice(dot).toLowerCase() : "";
  if (!limits.allowedExtensions.includes(ext)) {
    return `File type not allowed. Allowed: ${limits.allowedExtensions.join(", ")}.`;
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > limits.maxUploadBytes) {
    return `The file is larger than ${formatBytes(limits.maxUploadBytes)}.`;
  }
  return null;
}

/**
 * Wire up the upload panel.
 * @param {{
 *   root: HTMLElement,
 *   limits: UploadLimits,
 *   onUploaded: (doc: UploadedDoc) => void,
 * }} opts
 */
export function initUpload({ root, limits, onUploaded }) {
  const zone = /** @type {HTMLElement} */ (root.querySelector("[data-dropzone]"));
  const queue = /** @type {HTMLUListElement} */ (root.querySelector("[data-queue]"));
  const typeSelect = /** @type {HTMLSelectElement} */ (root.querySelector("[data-doc-type]"));
  const inputs = /** @type {NodeListOf<HTMLInputElement>} */ (root.querySelectorAll("input[type=file]"));

  /** @type {Array<{ file: File, row: QueueRow }>} */
  const pending = [];
  let busy = false;

  /** @param {Iterable<File>} files */
  const enqueue = (files) => {
    for (const file of files) {
      const row = queueRow(file);
      // Obvious problems are reported at once instead of waiting for earlier uploads.
      const problem = precheck(file, limits);
      if (problem) row.finish(problem, "error");
      else pending.push({ file, row });
    }
    if (!busy) void drain();
  };

  async function drain() {
    busy = true;
    while (pending.length) {
      const next = /** @type {{ file: File, row: QueueRow }} */ (pending.shift());
      await uploadOne(next.file, next.row);
    }
    busy = false;
  }

  /**
   * @typedef {{ progress: HTMLProgressElement, finish: (text: string, kind: "ok" | "error") => void }} QueueRow
   * @param {File} file
   * @returns {QueueRow}
   */
  function queueRow(file) {
    const progress = /** @type {HTMLProgressElement} */ (
      h("progress", { class: "progress", max: "100", value: "0",
        "aria-label": `Upload progress for ${file.name}` })
    );
    const message = h("span", { class: "queue__meta" }, `${formatBytes(file.size)} · waiting`);
    queue.prepend(
      h("li", { class: "queue__item" },
        h("span", { class: "queue__name", title: file.name }, file.name),
        progress,
        message,
      ),
    );
    return {
      progress,
      finish(text, kind) {
        progress.classList.add(kind === "ok" ? "progress--done" : "progress--error");
        message.className = `queue__message queue__message--${kind}`;
        replace(message, text);
      },
    };
  }

  /** @param {File} file @param {QueueRow} row */
  async function uploadOne(file, { progress, finish }) {
    try {
      const doc = await uploadFile(file, { doc_type: typeSelect.value }, (fraction) => {
        progress.value = Math.round(fraction * 100);
      });
      progress.value = 100;
      finish(doc.duplicate_of ? "Duplicate, not processed" : "Uploaded", "ok");
      if (doc.duplicate_of) {
        toast(`${file.name} matches a document already uploaded. It was not processed.`,
          { tone: "warn" });
      }
      onUploaded(doc);
    } catch (err) {
      const text = err instanceof ApiError ? err.detailText : "Upload failed.";
      finish(text, "error");
      if (err instanceof ApiError && err.status === 429) toast(text, { tone: "err" });
    }
  }

  for (const input of inputs) {
    input.addEventListener("change", () => {
      if (input.files) enqueue(Array.from(input.files));
      input.value = "";
    });
  }
  // Pick buttons may live outside the panel (e.g. the top bar's Upload button).
  for (const button of document.querySelectorAll("[data-pick]")) {
    const id = /** @type {HTMLElement} */ (button).dataset.pick ?? "";
    const target = /** @type {HTMLInputElement | null} */ (root.querySelector(`#${CSS.escape(id)}`));
    if (target) button.addEventListener("click", () => target.click());
  }

  // Drag and drop. A counter avoids flicker when dragging over child elements.
  let depth = 0;
  zone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    depth += 1;
    zone.classList.add("dropzone--active");
  });
  zone.addEventListener("dragover", (event) => event.preventDefault());
  zone.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) zone.classList.remove("dropzone--active");
  });
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    zone.classList.remove("dropzone--active");
    if (event.dataTransfer?.files.length) enqueue(Array.from(event.dataTransfer.files));
  });
}
