// Client-side running totals, computed in integer cents (BigInt) so they match the
// server's Decimal arithmetic exactly. Pure functions; unit-tested with node --test.
import { fromCents, toCents } from "./format.js";

const TOLERANCE = 1n; // one cent, same as the server's AMOUNT_TOLERANCE

/** @param {bigint} value */
const abs = (value) => (value < 0n ? -value : value);

/**
 * @typedef {{ cents: bigint | null, text: string }} Amount
 * @typedef {{ ok: boolean | null, difference: string | null }} Check
 */

/**
 * @param {Array<string | null | undefined>} values
 * @returns {{ sum: bigint, complete: boolean }}
 */
function sum(values) {
  let total = 0n;
  let complete = true;
  for (const value of values) {
    if (value === null || value === undefined || String(value).trim() === "") continue;
    const cents = toCents(value);
    if (cents === null) complete = false;
    else total += cents;
  }
  return { sum: total, complete };
}

/**
 * @param {bigint | null} expected
 * @param {bigint | null} stated
 * @returns {Check}
 */
function compare(expected, stated) {
  if (expected === null || stated === null) return { ok: null, difference: null };
  const diff = stated - expected;
  return { ok: abs(diff) <= TOLERANCE, difference: fromCents(diff) };
}

/**
 * Opening + movements vs stated closing balance.
 * Bank: +credits -debits. Credit card: +debits (charges) -credits (payments).
 * @param {{
 *   opening: string | null, closing: string | null, creditCard: boolean,
 *   lines: Array<{ debit: string | null, credit: string | null }>,
 * }} input
 */
export function statementTotals({ opening, closing, creditCard, lines }) {
  const debits = sum(lines.map((l) => l.debit));
  const credits = sum(lines.map((l) => l.credit));
  const open = toCents(opening);
  const stated = toCents(closing);
  const movement = creditCard ? debits.sum - credits.sum : credits.sum - debits.sum;
  const computed = open === null || !debits.complete || !credits.complete ? null : open + movement;
  return {
    opening: open === null ? null : fromCents(open),
    debits: fromCents(debits.sum),
    credits: fromCents(credits.sum),
    computed: computed === null ? null : fromCents(computed),
    stated: stated === null ? null : fromCents(stated),
    check: compare(computed, stated),
  };
}

/**
 * Line items vs subtotal, and subtotal + taxes vs total.
 * @param {{
 *   subtotal: string | null, total: string | null,
 *   taxes: Array<string | null>, lines: Array<{ amount: string | null }>,
 * }} input
 */
export function receiptTotals({ subtotal, total, taxes, lines }) {
  const lineSum = sum(lines.map((l) => l.amount));
  const tax = sum(taxes);
  const sub = toCents(subtotal);
  const stated = toCents(total);
  const computedTotal = sub === null || !tax.complete ? null : sub + tax.sum;
  return {
    lineSum: lines.length && lineSum.complete ? fromCents(lineSum.sum) : null,
    subtotal: sub === null ? null : fromCents(sub),
    tax: fromCents(tax.sum),
    computedTotal: computedTotal === null ? null : fromCents(computedTotal),
    total: stated === null ? null : fromCents(stated),
    linesCheck: lines.length && lineSum.complete ? compare(lineSum.sum, sub) : { ok: null, difference: null },
    totalCheck: compare(computedTotal, stated),
  };
}
