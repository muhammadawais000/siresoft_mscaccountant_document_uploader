import { test } from "node:test";
import assert from "node:assert/strict";
import { receiptTotals, statementTotals } from "../../static/document_intake/js/totals.js";

const BANK_LINES = [
  { debit: "1500.00", credit: null },
  { debit: null, credit: "700.00" },
];

test("bank statement balances", () => {
  const t = statementTotals({ opening: "5000.00", closing: "4200.00", creditCard: false, lines: BANK_LINES });
  assert.equal(t.debits, "1500.00");
  assert.equal(t.credits, "700.00");
  assert.equal(t.computed, "4200.00");
  assert.deepEqual(t.check, { ok: true, difference: "0.00" });
});

test("bank statement mismatch reports signed difference", () => {
  const t = statementTotals({ opening: "5000", closing: "4,300.00", creditCard: false, lines: BANK_LINES });
  assert.deepEqual(t.check, { ok: false, difference: "100.00" });
});

test("credit card reverses the signs", () => {
  const t = statementTotals({
    opening: "500.00", closing: "400.00", creditCard: true,
    lines: [{ debit: "200.00", credit: null }, { debit: null, credit: "300.00" }],
  });
  assert.equal(t.check.ok, true);
});

test("one-cent tolerance, exact cents", () => {
  const lines = [{ debit: null, credit: "0.10" }, { debit: null, credit: "0.20" }];
  assert.equal(statementTotals({ opening: "0", closing: "0.30", creditCard: false, lines }).check.ok, true);
  assert.equal(statementTotals({ opening: "0", closing: "0.31", creditCard: false, lines }).check.ok, true);
  assert.equal(statementTotals({ opening: "0", closing: "0.32", creditCard: false, lines }).check.ok, false);
});

test("unreadable or missing values give no verdict", () => {
  const bad = statementTotals({ opening: "5000", closing: "4200", creditCard: false,
    lines: [{ debit: "abc", credit: null }] });
  assert.equal(bad.computed, null);
  assert.equal(bad.check.ok, null);
  const noOpening = statementTotals({ opening: "", closing: "1", creditCard: false, lines: [] });
  assert.equal(noOpening.check.ok, null);
});

test("receipt totals", () => {
  const t = receiptTotals({
    subtotal: "100.00", total: "113.00", taxes: ["13.00", null, ""],
    lines: [{ amount: "60.00" }, { amount: "40.00" }],
  });
  assert.equal(t.lineSum, "100.00");
  assert.equal(t.computedTotal, "113.00");
  assert.equal(t.linesCheck.ok, true);
  assert.equal(t.totalCheck.ok, true);
});

test("receipt mismatches", () => {
  const t = receiptTotals({
    subtotal: "100.00", total: "114.00", taxes: ["13.00"],
    lines: [{ amount: "60.00" }, { amount: "30.00" }],
  });
  assert.deepEqual(t.linesCheck, { ok: false, difference: "10.00" });
  assert.deepEqual(t.totalCheck, { ok: false, difference: "1.00" });
});

test("receipt without lines skips the line check", () => {
  const t = receiptTotals({ subtotal: "100", total: "100", taxes: [], lines: [] });
  assert.equal(t.lineSum, null);
  assert.equal(t.linesCheck.ok, null);
  assert.equal(t.totalCheck.ok, true);
});
