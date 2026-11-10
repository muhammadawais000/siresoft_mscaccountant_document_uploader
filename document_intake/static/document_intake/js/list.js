// Documents list page: filters (synced to the URL), table, polling, upload.
import { api, ApiError } from "./api.js";
import { $, debounce, h, replace } from "./dom.js";
import { isInProgress } from "./format.js";
import { renderRows, renderSkeleton, initSorting } from "./table.js";
import { announce, toast } from "./toast.js";
import { initUpload } from "./upload.js";

/**
 * @typedef {{
 *   statuses: Record<string, string>, docTypes: Record<string, string>,
 *   reviewUrlTemplate: string, fileUrlTemplate: string,
 *   maxUploadBytes: number, allowedExtensions: string[], pollMs: number, pageSize: number,
 * }} PageConfig
 * @typedef {import("./table.js").DocumentRow} DocumentRow
 */

const FILTER_KEYS = ["search", "status", "doc_type", "created_from", "created_to", "ordering"];
const PLACEHOLDER = "00000000-0000-0000-0000-000000000000";

function main() {
  /** @type {PageConfig} */
  const cfg = JSON.parse($("#intake-config").textContent ?? "{}");
  const form = /** @type {HTMLFormElement} */ ($("[data-filters]"));
  const table = /** @type {HTMLTableElement} */ ($("[data-documents]"));
  const tbody = /** @type {HTMLTableSectionElement} */ (table.tBodies[0]);
  const summary = $("[data-summary]");
  const prev = /** @type {HTMLButtonElement} */ ($("[data-prev]"));
  const next = /** @type {HTMLButtonElement} */ ($("[data-next]"));

  const tableCfg = {
    statuses: cfg.statuses,
    docTypes: cfg.docTypes,
    reviewUrl: (/** @type {string} */ id) => cfg.reviewUrlTemplate.replace(PLACEHOLDER, id),
    fileUrl: (/** @type {string} */ id) => cfg.fileUrlTemplate.replace(PLACEHOLDER, id),
  };

  const url = new URL(window.location.href);
  let page = Math.max(1, Number(url.searchParams.get("page")) || 1);
  /** @type {Map<string, string>} */
  let lastStatus = new Map();
  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let pollTimer;
  /** @type {AbortController | undefined} */
  let inflight;

  // Restore filters from the URL so views are shareable and survive reloads.
  for (const key of FILTER_KEYS) {
    const field = /** @type {HTMLInputElement | null} */ (form.elements.namedItem(key));
    const value = url.searchParams.get(key);
    if (field && value !== null) field.value = value;
  }

  /** @returns {URLSearchParams} */
  function params() {
    const out = new URLSearchParams();
    for (const key of FILTER_KEYS) {
      const field = /** @type {HTMLInputElement | null} */ (form.elements.namedItem(key));
      if (field?.value) out.set(key, field.value);
    }
    if (page > 1) out.set("page", String(page));
    return out;
  }

  const ordering = () => {
    const field = /** @type {HTMLInputElement} */ (form.elements.namedItem("ordering"));
    return field.value || "-created_at";
  };

  /** @param {{ quiet?: boolean }} [opts] */
  async function load(opts = {}) {
    clearTimeout(pollTimer);
    inflight?.abort();
    inflight = new AbortController();
    const query = params();
    window.history.replaceState(null, "", `${window.location.pathname}?${query}`);
    if (!opts.quiet) renderSkeleton(tbody);

    try {
      const data = await api("documents/", { params: query, signal: inflight.signal });
      /** @type {DocumentRow[]} */
      const docs = data.results;
      const filtered = FILTER_KEYS.some((k) => k !== "ordering" && query.has(k));
      renderRows(tbody, docs, tableCfg, { filtered });
      announceChanges(docs);
      renderPager(data.count, docs.length);
      if (docs.some((d) => isInProgress(d.status))) {
        pollTimer = setTimeout(() => void load({ quiet: true }), cfg.pollMs);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      if (err instanceof ApiError && err.status === 404 && page > 1) {
        page = 1; // filters shrank the result set below the current page
        void load();
        return;
      }
      tbody.setAttribute("aria-busy", "false");
      replace(tbody, h("tr", {}, h("td", { colspan: 6 },
        h("div", { class: "empty" },
          h("div", { class: "empty__title" }, "Documents could not be loaded"),
          err instanceof ApiError ? err.detailText : "Unexpected error.",
        ))));
      if (!opts.quiet) toast("Documents could not be loaded.", { tone: "err" });
    }
  }

  /** Tell screen-reader users when a document finishes processing. @param {DocumentRow[]} docs */
  function announceChanges(docs) {
    const fresh = new Map(docs.map((d) => [d.id, d.status]));
    for (const doc of docs) {
      const before = lastStatus.get(doc.id);
      if (before && isInProgress(before) && !isInProgress(doc.status)) {
        const label = cfg.statuses[doc.status] ?? doc.status;
        announce(`${doc.original_filename}: ${label}`);
        if (doc.status === "failed") toast(`${doc.original_filename} could not be processed.`, { tone: "err" });
      }
    }
    lastStatus = fresh;
  }

  /** @param {number} count @param {number} shown */
  function renderPager(count, shown) {
    const start = count ? (page - 1) * cfg.pageSize + 1 : 0;
    const end = start ? start + shown - 1 : 0;
    replace(summary, count ? `${start}–${end} of ${count}` : "0 documents");
    prev.disabled = page <= 1;
    next.disabled = end >= count;
  }

  const reload = () => {
    page = 1;
    void load();
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    reload();
  });
  form.addEventListener("change", (event) => {
    if (/** @type {HTMLElement} */ (event.target).getAttribute("name") !== "search") reload();
  });
  $("[name=search]", form).addEventListener("input", debounce(reload, 300));
  form.addEventListener("reset", () => setTimeout(reload));

  prev.addEventListener("click", () => {
    page -= 1;
    void load();
  });
  next.addEventListener("click", () => {
    page += 1;
    void load();
  });

  initSorting(table, ordering, (value) => {
    const field = /** @type {HTMLInputElement} */ (form.elements.namedItem("ordering"));
    field.value = value;
    reload();
  });

  initUpload({
    root: $("[data-upload]"),
    limits: { maxUploadBytes: cfg.maxUploadBytes, allowedExtensions: cfg.allowedExtensions },
    onUploaded: () => {
      page = 1;
      void load({ quiet: true });
    },
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void load({ quiet: true });
  });

  void load();
}

main();
