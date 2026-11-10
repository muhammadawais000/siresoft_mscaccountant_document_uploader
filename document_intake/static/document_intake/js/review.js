// Review screen controller: header form, editable lines, live totals, validation panel,
// actions and the activity tab. All text from the server is rendered via textContent.
import { api, apiBase, ApiError } from "./api.js";
import { openDialog } from "./dialog.js";
import { $, debounce, h, replace } from "./dom.js";
import { formatDateTime, formatMoney, isInProgress, statusTone, toCents } from "./format.js";
import { initViewer } from "./preview.js";
import { announce, toast } from "./toast.js";
import { receiptTotals, statementTotals } from "./totals.js";

/**
 * @typedef {{ id: string, key: string, label: string, value: string, normalized_value: string,
 *   status: string, status_reason: string }} Field
 * @typedef {{ id: string, line_no: number, date: string | null, description: string,
 *   debit: string | null, credit: string | null, amount: string | null, balance: string | null,
 *   tax: string | null, status: string, status_reason: string }} Line
 * @typedef {{ id: string, rule_code: string, severity: string, field_key: string,
 *   line_no: number | null, message: string, resolved: boolean }} Issue
 * @typedef {{ id: string, version: number, doc_type: string, document_status: string,
 *   extraction_method: string, model_name: string, prompt_version: string, created_at: string,
 *   header_fields: Field[], lines: Line[], issues: Issue[] }} Result
 * @typedef {{ id: string, original_filename: string, status: string, effective_doc_type: string,
 *   doc_type: string, error_message: string, duplicate_of: string | null }} Doc
 * @typedef {{ documentId: string, listUrl: string, reviewUrlTemplate: string,
 *   statuses: Record<string, string>, docTypes: Record<string, string>,
 *   amountKeys: string[], dateKeys: string[], reviewerKeys: string[],
 *   methods: Record<string, string>, pollMs: number }} ReviewConfig
 * @typedef {{ key: string, label: string, kind: "date" | "text" | "money" }} Column
 */

const EDITABLE = ["needs_review", "needs_clarification"];
const STATEMENTS = ["bank_statement", "credit_card_statement"];
const STATUS_WORD = { warning: "Check", error: "Error", missing: "Missing" };
const PLACEHOLDER = "00000000-0000-0000-0000-000000000000";

/** @type {Record<string, Column[]>} */
const COLUMNS = {
  statement: [
    { key: "date", label: "Date", kind: "date" },
    { key: "description", label: "Description", kind: "text" },
    { key: "debit", label: "Debit", kind: "money" },
    { key: "credit", label: "Credit", kind: "money" },
    { key: "balance", label: "Balance", kind: "money" },
  ],
  receipt: [
    { key: "description", label: "Description", kind: "text" },
    { key: "amount", label: "Amount", kind: "money" },
    { key: "tax", label: "Tax", kind: "money" },
  ],
};

/**
 * Normalise what a reviewer typed into a money cell. Returns undefined when invalid.
 * @param {string} raw
 * @returns {string | null | undefined}
 */
function moneyInput(raw) {
  const text = raw.trim().replace(/[$\s]/g, "").replace(/^\((.*)\)$/, "-$1");
  if (!text) return null;
  const cents = toCents(text);
  if (cents === null) return undefined;
  return text.replace(/,/g, "");
}

/**
 * @param {string} raw
 * @returns {string | null | undefined}
 */
function dateInput(raw) {
  const text = raw.trim();
  if (!text) return null;
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : undefined;
}

function main() {
  /** @type {ReviewConfig} */
  const cfg = JSON.parse($("#review-config").textContent ?? "{}");
  const docPath = `documents/${cfg.documentId}/`;

  // ------------------------------------------------------------------ elements
  const statusBadge = $("[data-status-badge]");
  const notices = $("[data-notices]");
  const meta = $("[data-meta]");
  const saveState = $("[data-save-state]");
  const fieldsRoot = $("[data-fields]");
  const linesPanel = $("[data-lines-panel]");
  const linesTitle = $("[data-lines-title]");
  const linesHead = /** @type {HTMLTableSectionElement} */ ($("[data-lines] thead"));
  const linesBody = /** @type {HTMLTableSectionElement} */ ($("[data-lines] tbody"));
  const linesFoot = /** @type {HTMLTableSectionElement} */ ($("[data-lines] tfoot"));
  const addLineBtn = /** @type {HTMLButtonElement} */ ($("[data-add-line]"));
  const totalsRoot = $("[data-totals]");
  const issuesRoot = $("[data-issues]");
  const issuesSummary = $("[data-issues-summary]");
  const ackRow = $("[data-ack]");
  const ackBox = /** @type {HTMLInputElement} */ ($("[data-ack-box]"));
  const ackText = $("[data-ack-text]");
  const sidePanels = $("[data-side]");
  const btn = {
    save: /** @type {HTMLButtonElement} */ ($("[data-action=save]")),
    approve: /** @type {HTMLButtonElement} */ ($("[data-action=approve]")),
    reject: /** @type {HTMLButtonElement} */ ($("[data-action=reject]")),
    exportCsv: /** @type {HTMLAnchorElement} */ ($("[data-action=export]")),
    reprocess: /** @type {HTMLButtonElement} */ ($("[data-action=reprocess]")),
    clarify: /** @type {HTMLButtonElement} */ ($("[data-action=clarify]")),
    resume: /** @type {HTMLButtonElement} */ ($("[data-action=resume]")),
    duplicate: /** @type {HTMLButtonElement} */ ($("[data-action=duplicate]")),
  };

  // --------------------------------------------------------------------- state
  /** @type {Doc | null} */
  let doc = null;
  /** @type {Result | null} */
  let result = null;
  /** @type {Map<string, { input: HTMLInputElement, item: HTMLElement, reason: HTMLElement, field: Field }>} */
  const fieldEls = new Map();
  /** @type {Map<string, { row: HTMLTableRowElement, cells: Map<string, HTMLInputElement>, flag: HTMLElement }>} */
  const lineEls = new Map();
  /** @type {Map<string, string>} fieldId -> value */
  const pendingFields = new Map();
  /** @type {Map<string, Record<string, string | null>>} lineId -> changed columns */
  const pendingLines = new Map();
  let saving = Promise.resolve();
  let saveError = false;

  const editable = () => Boolean(doc && result && EDITABLE.includes(doc.status));
  const isStatement = () => STATEMENTS.includes(result?.doc_type ?? "");
  const columns = () => COLUMNS[isStatement() ? "statement" : "receipt"];
  const hasPending = () => pendingFields.size > 0 || pendingLines.size > 0;

  // -------------------------------------------------------------- save status
  function renderSaveState(/** @type {"saved" | "dirty" | "saving" | "error"} */ state) {
    const text = { saved: "All changes saved", dirty: "Unsaved changes", saving: "Saving…",
      error: "Some changes were not saved" }[state];
    saveState.className = `statusbar__save${state === "dirty" ? " statusbar__save--dirty" : ""}${
      state === "error" ? " statusbar__save--error" : ""}`;
    replace(saveState, text);
    btn.save.disabled = !editable() || (state === "saved" && !saveError);
  }

  const scheduleSave = debounce(() => void flush(), 700);

  /** Send pending edits one at a time; the last response becomes the visible result. */
  function flush() {
    saving = saving.then(async () => {
      if (!hasPending()) return;
      renderSaveState("saving");
      saveError = false;
      const fields = [...pendingFields];
      const lines = [...pendingLines];
      pendingFields.clear();
      pendingLines.clear();
      /** @type {Result | null} */
      let latest = null;
      for (const [fieldId, value] of fields) {
        try {
          latest = await api(`${docPath}fields/${fieldId}/`, { method: "PATCH", json: { value } });
        } catch (err) {
          saveError = true;
          reportSaveError(err);
        }
      }
      for (const [lineId, values] of lines) {
        try {
          latest = await api(`${docPath}lines/${lineId}/`, { method: "PATCH", json: values });
        } catch (err) {
          saveError = true;
          markLineInvalid(lineId, err);
        }
      }
      if (latest) applyResult(latest);
      renderSaveState(saveError ? "error" : hasPending() ? "dirty" : "saved");
    });
    return saving;
  }

  /** @param {unknown} err */
  function reportSaveError(err) {
    const text = err instanceof ApiError ? err.detailText : "The change could not be saved.";
    toast(text, { tone: "err" });
    if (err instanceof ApiError && err.status === 409) void reload();
  }

  /** @param {string} lineId @param {unknown} err */
  function markLineInvalid(lineId, err) {
    const els = lineEls.get(lineId);
    if (err instanceof ApiError && els) {
      for (const key of Object.keys(err.details)) {
        els.cells.get(key)?.setAttribute("aria-invalid", "true");
      }
    }
    reportSaveError(err);
  }

  // ------------------------------------------------------------------- fields
  /** @param {Field} field */
  function fieldKind(field) {
    if (cfg.amountKeys.includes(field.key)) return "money";
    if (cfg.dateKeys.includes(field.key)) return "date";
    return "text";
  }

  function renderFields() {
    fieldEls.clear();
    if (!result) return;
    replace(fieldsRoot, result.header_fields.map((field) => {
      const kind = fieldKind(field);
      const inputId = `field-${field.key}`;
      const reason = h("span", { class: "fields__reason", id: `${inputId}-reason` });
      const input = /** @type {HTMLInputElement} */ (h("input", {
        class: `input fields__input${kind === "money" ? " num" : ""}`,
        id: inputId,
        value: field.value,
        inputmode: kind === "money" ? "decimal" : null,
        placeholder: kind === "date" ? "YYYY-MM-DD" : kind === "money" ? "0.00" : null,
        autocomplete: "off",
        spellcheck: kind === "text" ? null : "false",
        "aria-describedby": `${inputId}-reason`,
        "data-key": field.key,
      }));
      input.addEventListener("input", () => {
        pendingFields.set(field.id, input.value);
        renderSaveState("dirty");
        renderTotals();
        scheduleSave();
      });
      input.addEventListener("blur", () => { if (pendingFields.has(field.id)) void flush(); });
      const item = h("div", { class: "field fields__item" },
        h("label", { class: "field__label", for: inputId }, field.label,
          cfg.reviewerKeys.includes(field.key) ? h("span", { class: "fields__tag" }, " · you enter this") : null),
        input,
        reason,
      );
      fieldEls.set(field.key, { input, item, reason, field });
      return item;
    }));
    applyFieldStates();
  }

  function applyFieldStates() {
    if (!result) return;
    for (const field of result.header_fields) {
      const els = fieldEls.get(field.key);
      if (!els) continue;
      els.field = field;
      els.item.classList.remove("is-warning", "is-error", "is-missing");
      if (field.status !== "ok") els.item.classList.add(`is-${field.status}`);
      els.input.disabled = !editable();
      els.input.setAttribute("aria-invalid", field.status === "error" ? "true" : "false");
      const word = STATUS_WORD[/** @type {keyof typeof STATUS_WORD} */ (field.status)];
      els.reason.className = `fields__reason${word ? ` fields__reason--${field.status}` : ""}`;
      replace(els.reason, word ? `${word}: ${field.status_reason || "Needs review."}` : "");
      // Never overwrite what the reviewer is typing.
      if (document.activeElement !== els.input && !pendingFields.has(field.id)) {
        els.input.value = field.value;
      }
    }
  }

  // -------------------------------------------------------------------- lines
  function renderLinesHead() {
    replace(linesTitle, isStatement() ? "Transactions" : "Line items");
    replace(linesHead, h("tr", {},
      h("th", { class: "lines__no", scope: "col" }, "#"),
      columns().map((c) => h("th", {
        scope: "col",
        class: c.kind === "money" ? "num lines__money" : c.kind === "date" ? "lines__date" : null,
      }, c.label)),
      h("th", { class: "lines__state", scope: "col" }, h("span", { class: "visually-hidden" }, "Status")),
      h("th", { class: "lines__remove", scope: "col" }, h("span", { class: "visually-hidden" }, "Remove")),
    ));
  }

  /** @param {Line} line */
  function lineRow(line) {
    /** @type {Map<string, HTMLInputElement>} */
    const cells = new Map();
    const flag = h("span", { class: "lines__flag" });
    const row = /** @type {HTMLTableRowElement} */ (h("tr", { id: `line-${line.line_no}` },
      h("td", { class: "lines__no" }, String(line.line_no)),
      columns().map((col) => {
        const value = /** @type {string | null} */ (line[/** @type {keyof Line} */ (col.key)] ?? null);
        const input = /** @type {HTMLInputElement} */ (h("input", {
          class: `lines__cell${col.kind === "money" ? " num" : ""}`,
          // Money cells show plain decimals so editing never fights with separators.
          value: value ?? "",
          inputmode: col.kind === "money" ? "decimal" : null,
          placeholder: col.kind === "date" ? "YYYY-MM-DD" : null,
          autocomplete: "off",
          "aria-label": `Line ${line.line_no} ${col.label}`,
          "data-col": col.key,
        }));
        input.addEventListener("input", () => onCellInput(line.id, col, input));
        input.addEventListener("blur", () => { if (pendingLines.has(line.id)) void flush(); });
        input.addEventListener("keydown", (event) => onCellKey(event, line.id, col.key));
        cells.set(col.key, input);
        return h("td", {}, input);
      }),
      h("td", { class: "lines__state" }, flag),
      h("td", { class: "lines__remove" },
        h("button", {
          class: "btn btn--sm btn--ghost", type: "button", "data-remove": true,
          "aria-label": `Remove line ${line.line_no}`, onclick: () => void removeLine(line),
        }, "×")),
    ));
    lineEls.set(line.id, { row, cells, flag });
    return row;
  }

  /**
   * @param {string} lineId
   * @param {Column} col
   * @param {HTMLInputElement} input
   */
  function onCellInput(lineId, col, input) {
    const parsed = col.kind === "money" ? moneyInput(input.value)
      : col.kind === "date" ? dateInput(input.value) : input.value;
    renderTotals();
    if (parsed === undefined) {
      input.setAttribute("aria-invalid", "true");
      input.title = col.kind === "money" ? "Enter an amount like 1234.56" : "Enter a date as YYYY-MM-DD";
      return;
    }
    input.removeAttribute("aria-invalid");
    input.title = "";
    pendingLines.set(lineId, { ...(pendingLines.get(lineId) ?? {}), [col.key]: parsed });
    renderSaveState("dirty");
    scheduleSave();
  }

  /**
   * Enter moves down a row in the same column (spreadsheet habit).
   * @param {KeyboardEvent} event @param {string} lineId @param {string} colKey
   */
  function onCellKey(event, lineId, colKey) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    const ids = [...lineEls.keys()];
    const nextId = ids[ids.indexOf(lineId) + (event.shiftKey ? -1 : 1)];
    lineEls.get(nextId ?? "")?.cells.get(colKey)?.focus();
  }

  function renderLines() {
    lineEls.clear();
    renderLinesHead();
    if (!result) return;
    if (!result.lines.length) {
      replace(linesBody, h("tr", {}, h("td", { colspan: columns().length + 3 },
        h("div", { class: "empty" },
          h("div", { class: "empty__title" }, isStatement() ? "No transactions" : "No line items"),
          editable() ? "Add rows the model missed with “Add row”." : ""))));
    } else {
      replace(linesBody, result.lines.map(lineRow));
    }
    applyLineStates();
  }

  function applyLineStates() {
    if (!result) return;
    const known = new Set(result.lines.map((l) => l.id));
    if ([...lineEls.keys()].some((id) => !known.has(id)) || result.lines.some((l) => !lineEls.has(l.id))) {
      renderLines(); // rows were added or removed: rebuild (keeps keyed state simple)
      return;
    }
    for (const line of result.lines) {
      const els = lineEls.get(line.id);
      if (!els) continue;
      els.row.classList.remove("is-warning", "is-error", "is-missing");
      const word = STATUS_WORD[/** @type {keyof typeof STATUS_WORD} */ (line.status)];
      if (word) els.row.classList.add(`is-${line.status}`);
      els.flag.className = word ? `lines__flag lines__flag--${line.status}` : "lines__flag";
      replace(els.flag, word ? "!" : "");
      els.flag.title = word ? `${word}: ${line.status_reason}` : "";
      els.flag.setAttribute("aria-label", word ? `${word}: ${line.status_reason}` : "OK");
      for (const [key, input] of els.cells) {
        input.disabled = !editable();
        const pending = pendingLines.get(line.id);
        if (document.activeElement !== input && !(pending && key in pending)) {
          input.value = /** @type {string | null} */ (line[/** @type {keyof Line} */ (key)] ?? null) ?? "";
          input.removeAttribute("aria-invalid");
        }
      }
      els.row.querySelector("[data-remove]")?.toggleAttribute("hidden", !editable());
    }
    addLineBtn.hidden = !editable();
  }

  async function addLine() {
    await flush();
    try {
      const fresh = /** @type {Result} */ (await api(`${docPath}lines/`, { method: "POST", json: {} }));
      applyResult(fresh);
      const last = fresh.lines[fresh.lines.length - 1];
      lineEls.get(last.id)?.cells.values().next().value?.focus();
    } catch (err) {
      reportSaveError(err);
    }
  }

  /** @param {Line} line */
  async function removeLine(line) {
    const ok = await openDialog({
      title: `Remove line ${line.line_no}?`,
      body: "The row is hidden from the result and the export. It stays in the audit history.",
      confirmLabel: "Remove line",
      danger: true,
    });
    if (!ok) return;
    await flush();
    try {
      applyResult(await api(`${docPath}lines/${line.id}/`, { method: "DELETE" }));
      announce(`Line ${line.line_no} removed`);
    } catch (err) {
      reportSaveError(err);
    }
  }

  // ------------------------------------------------------------------- totals
  /** Current value of a header field as typed (not yet saved). */
  const fieldValue = (/** @type {string} */ key) => fieldEls.get(key)?.input.value ?? null;

  /** Current values of every line, from the inputs. */
  function lineValues() {
    return [...lineEls.values()].map(({ cells }) => {
      /** @type {Record<string, string | null>} */
      const out = {};
      for (const [key, input] of cells) {
        out[key] = input.value.trim() ? moneyInput(input.value) ?? input.value : null;
      }
      return out;
    });
  }

  /**
   * @param {string} label @param {string | null} value
   * @param {{ op?: string, total?: boolean, cls?: string }} [opts]
   */
  function totalsRow(label, value, opts = {}) {
    return h("tr", { class: [opts.total ? "totals__total" : "", opts.cls ?? ""].join(" ").trim() || null },
      h("td", { class: "totals__op" }, opts.op ?? ""),
      h("th", { scope: "row" }, label),
      h("td", { class: "num" }, value === null ? "—" : formatMoney(value)));
  }

  /** @param {string} label @param {{ ok: boolean | null, difference: string | null }} check */
  function checkRow(label, check) {
    if (check.ok === null) {
      return h("tr", {}, h("td", { class: "totals__op" }), h("th", { scope: "row" }, label),
        h("td", { class: "num" }, "Cannot check"));
    }
    return h("tr", { class: check.ok ? "totals__diff--ok" : "totals__diff--bad" },
      h("td", { class: "totals__op" }, check.ok ? "✓" : "≠"),
      h("th", { scope: "row" }, label),
      h("td", { class: "num" }, check.ok ? "Matches" : `Off by ${formatMoney(check.difference)}`));
  }

  function renderTotals() {
    if (!result) return;
    const lines = lineValues();
    if (isStatement()) {
      const creditCard = result.doc_type === "credit_card_statement";
      const t = statementTotals({
        opening: fieldValue("opening_balance"),
        closing: fieldValue("closing_balance"),
        creditCard,
        lines: lines.map((l) => ({ debit: l.debit ?? null, credit: l.credit ?? null })),
      });
      replace(totalsRoot, h("table", { class: "totals" }, h("tbody", {},
        totalsRow("Opening balance", t.opening),
        totalsRow(creditCard ? "Payments and credits" : "Credits (money in)", t.credits,
          { op: creditCard ? "−" : "+" }),
        totalsRow(creditCard ? "Purchases and charges" : "Debits (money out)", t.debits,
          { op: creditCard ? "+" : "−" }),
        totalsRow("Calculated closing balance", t.computed, { op: "=", total: true }),
        totalsRow("Closing balance on statement", t.stated),
        checkRow("Difference", t.check),
      )));
      replace(linesFoot, h("tr", {},
        h("td", {}), h("td", {}), h("td", {}, "Totals"),
        h("td", { class: "num" }, formatMoney(t.debits)),
        h("td", { class: "num" }, formatMoney(t.credits)),
        h("td", {}), h("td", {}), h("td", {})));
    } else {
      const t = receiptTotals({
        subtotal: fieldValue("subtotal"),
        total: fieldValue("total"),
        taxes: ["gst", "hst", "pst"].map(fieldValue),
        lines: lines.map((l) => ({ amount: l.amount ?? null })),
      });
      replace(totalsRoot, h("table", { class: "totals" }, h("tbody", {},
        totalsRow("Line items", t.lineSum),
        totalsRow("Subtotal on document", t.subtotal),
        checkRow("Lines vs subtotal", t.linesCheck),
        totalsRow("Taxes (GST + HST + PST)", t.tax, { op: "+" }),
        totalsRow("Calculated total", t.computedTotal, { op: "=", total: true }),
        totalsRow("Total on document", t.total),
        checkRow("Difference", t.totalCheck),
      )));
      replace(linesFoot, h("tr", {},
        h("td", {}), h("td", {}, "Totals"),
        h("td", { class: "num" }, t.lineSum === null ? "—" : formatMoney(t.lineSum)),
        h("td", {}), h("td", {}), h("td", {})));
    }
  }

  // ------------------------------------------------------------------- issues
  /** @param {Issue} issue */
  function issueLocation(issue) {
    if (issue.line_no !== null) return `Line ${issue.line_no}`;
    return fieldEls.get(issue.field_key)?.field.label ?? "Document";
  }

  /** @param {Issue} issue */
  function jumpTo(issue) {
    /** @type {HTMLElement | undefined} */
    let target;
    if (issue.line_no !== null) {
      const els = [...lineEls.values()].find((e) => e.row.id === `line-${issue.line_no}`);
      target = els?.cells.values().next().value;
      els?.row.scrollIntoView({ block: "center", behavior: "smooth" });
    } else {
      target = fieldEls.get(issue.field_key)?.input;
      target?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    if (!target) return;
    target.focus({ preventScroll: true });
    target.classList.remove("is-target");
    void target.offsetWidth; // restart the highlight animation
    target.classList.add("is-target");
  }

  /** @param {Issue} issue */
  async function toggleResolved(issue) {
    try {
      applyResult(await api(`${docPath}issues/${issue.id}/`, {
        method: "PATCH", json: { resolved: !issue.resolved },
      }));
    } catch (err) {
      reportSaveError(err);
    }
  }

  function renderIssues() {
    if (!result) return;
    const open = result.issues.filter((i) => !i.resolved);
    const errors = open.filter((i) => i.severity === "error").length;
    const warnings = open.length - errors;
    replace(issuesSummary, open.length
      ? [errors && `${errors} error${errors === 1 ? "" : "s"}`,
        warnings && `${warnings} warning${warnings === 1 ? "" : "s"}`].filter(Boolean).join(" · ")
      : "No open issues");

    if (!result.issues.length) {
      replace(issuesRoot, h("li", { class: "empty" },
        h("div", { class: "empty__title" }, "All checks passed"),
        "Totals and balances are consistent. Review the values against the document."));
    } else {
      replace(issuesRoot, result.issues.map((issue) => {
        const tone = issue.severity === "error" ? "err" : issue.severity === "warning" ? "warn" : "info";
        return h("li", { class: `issues__item${issue.resolved ? " issues__item--resolved" : ""}` },
          h("span", { class: `badge badge--${tone}` }, issue.severity === "error" ? "Error"
            : issue.severity === "warning" ? "Warning" : "Info"),
          h("button", { class: "issues__jump", type: "button", onclick: () => jumpTo(issue) },
            h("span", { class: "issues__where" }, issueLocation(issue)),
            h("span", { class: "issues__message" }, issue.message)),
          editable()
            ? h("button", {
              class: "btn btn--sm", type: "button", onclick: () => void toggleResolved(issue),
              "aria-label": `${issue.resolved ? "Reopen" : "Acknowledge"}: ${issue.message}`,
            }, issue.resolved ? "Reopen" : "Acknowledge")
            : h("span", {}),
        );
      }));
    }

    ackRow.hidden = !(editable() && errors > 0);
    replace(ackText, `I have checked the ${errors} open error${errors === 1 ? "" : "s"} against the document and want to approve anyway.`);
    if (!errors) ackBox.checked = false;
    updateActions();
  }

  // ------------------------------------------------------------------ actions
  function openErrors() {
    return result ? result.issues.filter((i) => !i.resolved && i.severity === "error").length : 0;
  }

  function updateActions() {
    const status = doc?.status ?? "";
    const reviewable = editable();
    btn.approve.disabled = !reviewable || (openErrors() > 0 && !ackBox.checked);
    btn.approve.title = btn.approve.disabled && reviewable
      ? "Resolve or acknowledge the open errors first" : "";
    btn.reject.disabled = !["needs_review", "needs_clarification", "duplicate", "failed"].includes(status);
    btn.reprocess.disabled = !["needs_review", "needs_clarification", "duplicate", "failed"].includes(status);
    btn.clarify.disabled = status !== "needs_review";
    btn.resume.hidden = status !== "needs_clarification";
    btn.duplicate.disabled = status !== "needs_review";
    const exportable = status === "approved" || status === "exported";
    btn.exportCsv.toggleAttribute("aria-disabled", !exportable);
    btn.exportCsv.tabIndex = exportable ? 0 : -1;
  }

  /**
   * Run an action endpoint, then reload everything.
   * @param {string} path @param {unknown} [json] @param {string} [done]
   */
  async function act(path, json, done) {
    await flush();
    try {
      await api(`${docPath}${path}`, { method: "POST", json: json ?? {} });
      if (done) toast(done, { tone: "ok" });
      await reload();
    } catch (err) {
      reportSaveError(err);
    }
  }

  btn.save.addEventListener("click", () => void flush());
  ackBox.addEventListener("change", updateActions);
  addLineBtn.addEventListener("click", () => void addLine());

  btn.approve.addEventListener("click", async () => {
    const errors = openErrors();
    const answer = await openDialog({
      title: "Approve this document?",
      body: errors
        ? `${errors} open error${errors === 1 ? " is" : "s are"} acknowledged and will be recorded with your approval.`
        : "The data becomes ready for export. Editing closes after approval.",
      confirmLabel: "Approve",
      input: { label: "Note (optional)", multiline: true },
    });
    if (answer) {
      await act("approve/", { acknowledge_errors: errors > 0 && ackBox.checked, note: answer.value },
        "Document approved.");
    }
  });

  btn.reject.addEventListener("click", async () => {
    const answer = await openDialog({
      title: "Reject this document?",
      body: "Rejected documents are kept but cannot be approved or exported.",
      confirmLabel: "Reject",
      danger: true,
      input: { label: "Reason (required)", required: true, multiline: true },
    });
    if (answer) await act("reject/", { reason: answer.value }, "Document rejected.");
  });

  btn.reprocess.addEventListener("click", async () => {
    const answer = await openDialog({
      title: "Run extraction again?",
      body: "A new version is created from the original file. The current version and your edits stay in the history.",
      confirmLabel: "Reprocess",
    });
    if (answer) await act("reprocess/", {}, "Extraction queued.");
  });

  btn.clarify.addEventListener("click", async () => {
    const answer = await openDialog({
      title: "Ask the client",
      body: "Review pauses until the question is answered.",
      confirmLabel: "Send question",
      input: { label: "Question", required: true, multiline: true },
    });
    if (answer) await act("clarify/", { question: answer.value }, "Marked as needing clarification.");
  });

  btn.resume.addEventListener("click", () => void act("resume-review/", {}, "Back in review."));

  btn.duplicate.addEventListener("click", async () => {
    const answer = await openDialog({
      title: "Mark as duplicate?",
      body: "Use this when the same document was already uploaded. It will not be exported.",
      confirmLabel: "Mark duplicate",
      danger: true,
    });
    if (answer) await act("mark-duplicate/", { duplicate_of: doc?.duplicate_of ?? null }, "Marked as duplicate.");
  });

  btn.exportCsv.addEventListener("click", (event) => {
    if (btn.exportCsv.hasAttribute("aria-disabled")) {
      event.preventDefault();
      return;
    }
    // The download starts natively; refresh shortly after to show the "Exported" status.
    setTimeout(() => void reload(), 1200);
  });

  // Close open <details> menus on outside click / Escape.
  document.addEventListener("click", (event) => {
    for (const menu of document.querySelectorAll("details.menu[open]")) {
      if (!menu.contains(/** @type {Node} */ (event.target))) menu.removeAttribute("open");
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      for (const menu of document.querySelectorAll("details.menu[open]")) menu.removeAttribute("open");
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (hasPending()) {
      void flush();
      event.preventDefault();
    }
  });

  // ------------------------------------------------------------------ loading
  /** @param {Result} fresh */
  function applyResult(fresh) {
    const typeChanged = !result || result.doc_type !== fresh.doc_type || result.id !== fresh.id;
    result = fresh;
    if (doc) doc.status = fresh.document_status;
    if (typeChanged || fieldEls.size === 0) {
      renderFields();
      renderLines();
    } else {
      applyFieldStates();
      applyLineStates();
    }
    renderTotals();
    renderIssues();
    renderHeader();
  }

  function renderHeader() {
    if (!doc) return;
    statusBadge.className = `badge badge--${statusTone(doc.status)}`;
    replace(statusBadge, cfg.statuses[doc.status] ?? doc.status);
    const type = doc.effective_doc_type || doc.doc_type;
    replace(meta,
      h("span", {}, cfg.docTypes[type] ?? type),
      result ? h("span", {}, `Version ${result.version}`) : null,
      result ? h("span", {}, cfg.methods[result.extraction_method] ?? result.extraction_method) : null,
      result ? h("span", {}, `Model ${result.model_name}`) : null,
      result ? h("span", {}, `Extracted ${formatDateTime(result.created_at)}`) : null,
    );
    renderNotices();
    updateActions();
    renderSaveState(hasPending() ? "dirty" : saveError ? "error" : "saved");
  }

  function renderNotices() {
    if (!doc) return;
    /** @type {HTMLElement[]} */
    const items = [];
    const s = doc.status;
    if (isInProgress(s)) {
      items.push(h("div", { class: "notice notice--info", role: "status" },
        "Extraction is in progress. This page updates automatically."));
    } else if (s === "failed") {
      items.push(h("div", { class: "notice notice--err" },
        `Extraction failed: ${doc.error_message || "unknown reason"}. Fix the cause and choose Reprocess.`));
    } else if (s === "duplicate") {
      items.push(h("div", { class: "notice notice--warn" },
        "This file is a duplicate of an earlier upload. ",
        doc.duplicate_of
          ? h("a", { href: cfg.reviewUrlTemplate.replace(PLACEHOLDER, doc.duplicate_of) }, "Open the original")
          : null,
        ". Choose Reprocess to extract it anyway."));
    } else if (s === "needs_clarification") {
      items.push(h("div", { class: "notice notice--warn" },
        "Waiting for the client to answer a question. You can still edit; resume review when answered."));
    } else if (!EDITABLE.includes(s)) {
      items.push(h("div", { class: "notice notice--info" },
        `This document is ${(cfg.statuses[s] ?? s).toLowerCase()}. Editing is closed.`));
    }
    replace(notices, items);
  }

  /** @type {ReturnType<typeof setTimeout> | undefined} */
  let pollTimer;

  async function reload() {
    clearTimeout(pollTimer);
    try {
      doc = /** @type {Doc} */ (await api(docPath));
    } catch (err) {
      replace(notices, h("div", { class: "notice notice--err" },
        err instanceof ApiError ? err.detailText : "The document could not be loaded."));
      return;
    }
    if (isInProgress(doc.status)) {
      result = null;
      sidePanels.setAttribute("aria-busy", "true");
      renderHeader();
      pollTimer = setTimeout(() => void reload(), cfg.pollMs);
      return;
    }
    sidePanels.setAttribute("aria-busy", "false");
    try {
      const fresh = /** @type {Result} */ (await api(`${docPath}result/`));
      result = null; // full re-render after status changes
      applyResult(fresh);
      sidePanels.hidden = false;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        sidePanels.hidden = true; // e.g. a duplicate that was never extracted
        renderHeader();
      } else {
        toast("The extraction result could not be loaded.", { tone: "err" });
      }
    }
  }

  // ----------------------------------------------------------------- activity
  /** @param {{ action: string, metadata: Record<string, any> }} entry */
  function describe(entry) {
    const m = entry.metadata ?? {};
    const status = (/** @type {string} */ s) => cfg.statuses[s] ?? s;
    switch (entry.action) {
      case "upload": return `Uploaded (${m.mime_type ?? "file"})${m.duplicate_of ? ", duplicate of an earlier upload" : ""}`;
      case "extract": return m.outcome === "success"
        ? `Version ${m.version} · ${cfg.methods[m.method] ?? m.method} · ${m.model} · ${m.lines} lines · ${m.issues} issues`
        : "Extraction failed";
      case "status_change": return `${status(m.from)} → ${status(m.to)}${m.reason ? ` · ${m.reason}` : ""}${m.question ? ` · “${m.question}”` : ""}`;
      case "edit":
        if (m.target === "field") return `${m.key}: “${m.before}” → “${m.after}”`;
        if (m.target === "issue") return `${m.rule_code} ${m.resolved ? "acknowledged" : "reopened"}`;
        if (m.operation === "add") return `Added line ${m.line_no}`;
        if (m.operation === "delete") return `Removed line ${m.line_no}`;
        return `Line ${m.line_no}: ${Object.keys(m.after ?? {})
          .map((k) => `${k} “${m.before?.[k] ?? ""}” → “${m.after[k] ?? ""}”`).join(", ")}`;
      case "approve": return `Approved${m.acknowledged?.length ? ` · acknowledged ${m.acknowledged.join(", ")}` : ""}${m.note ? ` · ${m.note}` : ""}`;
      case "reject": return `Rejected · ${m.reason ?? ""}`;
      case "download": return m.disposition === "attachment" ? "Downloaded the original" : "Viewed the original";
      case "export": return `Exported CSV (${m.rows} rows, version ${m.version})`;
      case "reprocess": return "Requested a new extraction";
      default: return entry.action;
    }
  }

  async function loadActivity() {
    const list = $("[data-activity]");
    replace(list, h("li", { class: "activity__item" }, h("span", { class: "skeleton skeleton--w60" })));
    try {
      const entries = await api(`${docPath}activity/`);
      replace(list, entries.length ? entries.map((/** @type {any} */ e) =>
        h("li", { class: "activity__item" },
          h("span", { class: "activity__when" }, formatDateTime(e.timestamp)),
          h("span", { class: "activity__what" }, e.actor),
          h("span", { class: "activity__detail" }, describe(e)),
        )) : h("li", { class: "empty" }, "No activity yet."));
    } catch {
      replace(list, h("li", { class: "empty" }, "Activity could not be loaded."));
    }
  }

  // --------------------------------------------------------------------- tabs
  const tabs = /** @type {NodeListOf<HTMLButtonElement>} */ (document.querySelectorAll("[role=tab]"));
  /** @param {HTMLButtonElement} tab */
  function selectTab(tab) {
    for (const t of tabs) {
      const selected = t === tab;
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
      $(`#${t.getAttribute("aria-controls")}`).hidden = !selected;
    }
    if (tab.dataset.tab === "activity") void loadActivity();
  }
  for (const tab of tabs) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      const list = [...tabs];
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      const next = list[(list.indexOf(tab) + step + list.length) % list.length];
      next.focus();
      selectTab(next);
    });
  }

  // --------------------------------------------------------------------- boot
  btn.exportCsv.href = new URL(`${docPath}export.csv`, new URL(apiBase(), location.origin)).toString();
  void initViewer({ root: $("[data-viewer]"), documentPath: docPath, filename: $("[data-filename]").textContent ?? "" });
  void reload();
}

main();
