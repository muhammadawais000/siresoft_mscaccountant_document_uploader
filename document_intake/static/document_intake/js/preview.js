// Document viewer: server-rendered page images with page switching and zoom.
import { api } from "./api.js";
import { h, replace } from "./dom.js";

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

/**
 * @param {{ root: HTMLElement, documentPath: string, filename: string }} opts
 *   documentPath: API path of the document, e.g. "documents/<id>/"
 */
export async function initViewer({ root, documentPath, filename }) {
  const stage = /** @type {HTMLElement} */ (root.querySelector("[data-stage]"));
  const label = /** @type {HTMLElement} */ (root.querySelector("[data-page-label]"));
  const zoomLabel = /** @type {HTMLElement} */ (root.querySelector("[data-zoom-label]"));
  const prev = /** @type {HTMLButtonElement} */ (root.querySelector("[data-page-prev]"));
  const next = /** @type {HTMLButtonElement} */ (root.querySelector("[data-page-next]"));
  const zoomOut = /** @type {HTMLButtonElement} */ (root.querySelector("[data-zoom-out]"));
  const zoomIn = /** @type {HTMLButtonElement} */ (root.querySelector("[data-zoom-in]"));
  const zoomFit = /** @type {HTMLButtonElement} */ (root.querySelector("[data-zoom-fit]"));

  let page = 1;
  let count = 1;
  let zoomIndex = ZOOMS.indexOf(1);
  const base = new URL(documentPath, new URL(document.body.dataset.apiBase ?? "/", location.origin));

  try {
    count = (await api(`${documentPath}pages/`)).count || 1;
  } catch {
    replace(stage, h("div", { class: "notice notice--err" }, "The document preview could not be loaded."));
    return;
  }

  function render() {
    const img = /** @type {HTMLImageElement} */ (h("img", {
      class: "viewer__page",
      alt: `${filename}, page ${page} of ${count}`,
      // Higher DPI when zoomed in so text stays sharp.
      src: new URL(`pages/${page}/?dpi=${ZOOMS[zoomIndex] > 1.25 ? 200 : 130}`, base).toString(),
    }));
    img.addEventListener("error", () => {
      replace(stage, h("div", { class: "notice notice--err" }, "This page could not be rendered."));
    });
    replace(stage, img);
    stage.scrollTop = 0;
    replace(label, `Page ${page} of ${count}`);
    prev.disabled = page <= 1;
    next.disabled = page >= count;
    applyZoom();
  }

  function applyZoom() {
    stage.style.setProperty("--zoom", String(ZOOMS[zoomIndex]));
    replace(zoomLabel, `${Math.round(ZOOMS[zoomIndex] * 100)}%`);
    zoomOut.disabled = zoomIndex === 0;
    zoomIn.disabled = zoomIndex === ZOOMS.length - 1;
  }

  prev.addEventListener("click", () => { page -= 1; render(); });
  next.addEventListener("click", () => { page += 1; render(); });
  zoomOut.addEventListener("click", () => { zoomIndex -= 1; applyZoom(); });
  zoomIn.addEventListener("click", () => {
    const sharpen = ZOOMS[zoomIndex] <= 1.25 && ZOOMS[zoomIndex + 1] > 1.25;
    zoomIndex += 1;
    if (sharpen) render(); else applyZoom();
  });
  zoomFit.addEventListener("click", () => { zoomIndex = ZOOMS.indexOf(1); render(); });

  // Keyboard: PageUp/PageDown switch pages while the viewer has focus.
  root.addEventListener("keydown", (event) => {
    if (event.key === "PageDown" && page < count) { page += 1; render(); event.preventDefault(); }
    if (event.key === "PageUp" && page > 1) { page -= 1; render(); event.preventDefault(); }
  });

  render();
}
