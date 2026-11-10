// Formatting helpers. Pure functions (no DOM), unit-tested with node --test.

const LOCALE = "en-CA";
const TIME_ZONE = "America/Toronto";

/**
 * Format a decimal string for display: thousands separators, two decimals, negatives in
 * parentheses (accounting style). Works on strings to avoid float rounding of the input.
 * @param {string | null | undefined} value  e.g. "-1234.5"
 * @returns {string}  e.g. "(1,234.50)"; "" for null/blank; the input if not numeric
 */
export function formatMoney(value) {
  if (value === null || value === undefined || String(value).trim() === "") return "";
  const text = String(value).trim();
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text;
  const negative = text.startsWith("-");
  const [whole, frac = ""] = text.replace("-", "").split(".");
  const cents = (frac + "00").slice(0, 2);
  // Group digits on the string itself: exact for any length, no float conversion.
  const grouped = (whole.replace(/^0+(?=\d)/, "")).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const body = `${grouped}.${cents}`;
  return negative && body !== "0.00" ? `(${body})` : body;
}

/**
 * Parse a decimal string to integer cents, exactly (no floating point).
 * @param {string | null | undefined} value
 * @returns {bigint | null}
 */
export function toCents(value) {
  if (value === null || value === undefined) return null;
  const text = String(value).trim().replace(/,/g, "");
  const match = /^(-)?(\d+)(?:\.(\d{1,2}))?$/.exec(text);
  if (!match) return null;
  const cents = BigInt(match[2]) * 100n + BigInt((match[3] ?? "").padEnd(2, "0") || "0");
  return match[1] ? -cents : cents;
}

/**
 * Integer cents back to a decimal string.
 * @param {bigint} cents
 * @returns {string}
 */
export function fromCents(cents) {
  const negative = cents < 0n;
  const abs = negative ? -cents : cents;
  const whole = abs / 100n;
  const frac = String(abs % 100n).padStart(2, "0");
  return `${negative ? "-" : ""}${whole}.${frac}`;
}

/**
 * @param {number} bytes
 * @returns {string}
 */
export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const dateTimeFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIME_ZONE,
  year: "numeric",
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const dateFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: "UTC",
  year: "numeric",
  month: "short",
  day: "numeric",
});

/**
 * ISO timestamp -> "Nov 10, 2026, 09:05 a.m." in Toronto time.
 * @param {string | null | undefined} iso
 */
export function formatDateTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "" : dateTimeFmt.format(date);
}

/**
 * ISO calendar date "2026-09-03" -> "Sep 3, 2026". Treated as a calendar date (no zone).
 * @param {string | null | undefined} iso
 */
export function formatDate(iso) {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso ?? "";
  return dateFmt.format(new Date(`${iso}T00:00:00Z`));
}

/** @typedef {"ok" | "warn" | "err" | "info" | "neutral"} Tone */

/** @type {Record<string, Tone>} */
const STATUS_TONES = {
  uploaded: "info",
  processing: "info",
  needs_review: "warn",
  needs_clarification: "warn",
  approved: "ok",
  exported: "ok",
  rejected: "neutral",
  duplicate: "neutral",
  failed: "err",
};

/**
 * Tone for a document status badge.
 * @param {string} status
 * @returns {Tone}
 */
export function statusTone(status) {
  return STATUS_TONES[status] ?? "neutral";
}

/**
 * True while the server is still working on the document.
 * @param {string} status
 */
export function isInProgress(status) {
  return status === "uploaded" || status === "processing";
}
