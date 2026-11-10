// Documents table rendering: rows, skeleton, empty state, sortable headers.
import { h, replace } from "./dom.js";
import { formatDateTime, statusTone } from "./format.js";

/**
 * @typedef {{
 *   id: string, original_filename: string, doc_type: string, effective_doc_type: string,
 *   status: string, created_at: string, open_issues: number, open_errors: number,
 *   duplicate_of: string | null, error_message: string, mime_type: string,
 * }} DocumentRow
 * @typedef {{
 *   statuses: Record<string, string>, docTypes: Record<string, string>,
 *   reviewUrl: (id: string) => string, fileUrl: (id: string) => string,
 * }} TableConfig
 */

const COLUMNS = 6;

/**
 * @param {string} status
 * @param {Record<string, string>} labels
 */
export function statusBadge(status, labels) {
  return h("span", { class: `badge badge--${statusTone(status)}` }, labels[status] ?? status);
}

/**
 * @param {DocumentRow} doc
 */
function issuesCell(doc) {
  if (!doc.open_issues) return h("td", { class: "num" }, "—");
  const tone = doc.open_errors ? "err" : "warn";
  const label = doc.open_errors
    ? `${doc.open_issues} (${doc.open_errors} error${doc.open_errors === 1 ? "" : "s"})`
    : String(doc.open_issues);
  return h("td", { class: "num" }, h("span", { class: `badge badge--${tone}` }, label));
}

/**
 * @param {DocumentRow} doc
 * @param {TableConfig} cfg
 */
function row(doc, cfg) {
  const type = doc.effective_doc_type || doc.doc_type;
  const reviewable = !["uploaded", "processing"].includes(doc.status);
  const sub = doc.status === "failed" ? doc.error_message
    : doc.duplicate_of ? "Same file as an earlier upload" : "";
  return h("tr", { "data-id": doc.id },
    h("td", { class: "data-table__file" },
      reviewable
        ? h("a", { href: cfg.reviewUrl(doc.id), title: doc.original_filename }, doc.original_filename)
        : h("span", { title: doc.original_filename }, doc.original_filename),
      sub ? h("span", { class: "data-table__sub" }, sub) : null,
    ),
    h("td", {}, cfg.docTypes[type] ?? type),
    h("td", {}, statusBadge(doc.status, cfg.statuses)),
    h("td", { class: "num" }, formatDateTime(doc.created_at)),
    issuesCell(doc),
    h("td", { class: "data-table__actions" },
      reviewable
        ? h("a", { class: "btn btn--sm", href: cfg.reviewUrl(doc.id) }, "Review")
        : null,
      " ",
      h("a", {
        class: "btn btn--sm btn--ghost",
        href: `${cfg.fileUrl(doc.id)}?download=1`,
        "aria-label": `Download ${doc.original_filename}`,
      }, "Download"),
    ),
  );
}

/**
 * @param {HTMLTableSectionElement} tbody
 * @param {DocumentRow[]} docs
 * @param {TableConfig} cfg
 * @param {{ filtered: boolean }} state
 */
export function renderRows(tbody, docs, cfg, state) {
  tbody.setAttribute("aria-busy", "false");
  if (!docs.length) {
    replace(tbody, h("tr", {}, h("td", { colspan: COLUMNS },
      h("div", { class: "empty" },
        h("div", { class: "empty__title" },
          state.filtered ? "No documents match these filters" : "No documents yet"),
        state.filtered ? "Clear the filters to see all documents."
          : "Upload a bank statement, card statement, receipt or invoice to get started.",
      ),
    )));
    return;
  }
  replace(tbody, docs.map((doc) => row(doc, cfg)));
}

/**
 * Placeholder rows while loading; the page stays usable (no blocking spinner).
 * @param {HTMLTableSectionElement} tbody
 * @param {number} [count]
 */
export function renderSkeleton(tbody, count = 6) {
  tbody.setAttribute("aria-busy", "true");
  const widths = [70, 60, 50, 80, 30, 60];
  replace(tbody, Array.from({ length: count }, () =>
    h("tr", {}, widths.map((w) => h("td", {}, h("span", { class: `skeleton skeleton--w${w}` })))),
  ));
}

/**
 * Wire sortable headers. Each `th[data-sort]` holds a button; `aria-sort` reflects state.
 * @param {HTMLTableElement} table
 * @param {() => string} current  e.g. "-created_at"
 * @param {(ordering: string) => void} onSort
 */
export function initSorting(table, current, onSort) {
  const headers = /** @type {NodeListOf<HTMLElement>} */ (table.querySelectorAll("th[data-sort]"));
  const sync = () => {
    const ordering = current();
    for (const th of headers) {
      const key = th.dataset.sort ?? "";
      th.setAttribute("aria-sort",
        ordering === key ? "ascending" : ordering === `-${key}` ? "descending" : "none");
    }
  };
  for (const th of headers) {
    th.querySelector("button")?.addEventListener("click", () => {
      const key = th.dataset.sort ?? "";
      onSort(current() === key ? `-${key}` : key);
      sync();
    });
  }
  sync();
  return sync;
}
