// Run with: make jstest  (node --test, no dependencies)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  formatBytes,
  formatDate,
  formatMoney,
  fromCents,
  isInProgress,
  statusTone,
  toCents,
} from "../../static/document_intake/js/format.js";

test("formatMoney groups thousands and keeps two decimals", () => {
  assert.equal(formatMoney("1234.5"), "1,234.50");
  assert.equal(formatMoney("1234567.89"), "1,234,567.89");
  assert.equal(formatMoney("0"), "0.00");
  assert.equal(formatMoney("000123.4"), "123.40");
  assert.equal(formatMoney("999999999999.99"), "999,999,999,999.99");
});

test("formatMoney shows negatives in parentheses", () => {
  assert.equal(formatMoney("-1500.00"), "(1,500.00)");
  assert.equal(formatMoney("-0.00"), "0.00");
});

test("formatMoney passes through blanks and non-numbers", () => {
  assert.equal(formatMoney(null), "");
  assert.equal(formatMoney(""), "");
  assert.equal(formatMoney(">$12"), ">$12");
});

test("cents arithmetic is exact", () => {
  assert.equal(toCents("0.10") + toCents("0.20"), toCents("0.30"));
  assert.equal(toCents("1,234.5"), 123450n);
  assert.equal(toCents("-12.34"), -1234n);
  assert.equal(toCents("12"), 1200n);
  assert.equal(toCents("1.234"), null);
  assert.equal(toCents("abc"), null);
  assert.equal(toCents(null), null);
  assert.equal(fromCents(-1234n), "-12.34");
  assert.equal(fromCents(5n), "0.05");
});

test("formatBytes", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2048), "2 KB");
  assert.equal(formatBytes(25 * 1024 * 1024), "25.0 MB");
  assert.equal(formatBytes(-1), "");
});

test("formatDate treats ISO dates as calendar dates", () => {
  assert.equal(formatDate("2026-09-03"), "Sep 3, 2026");
  assert.equal(formatDate("not a date"), "not a date");
  assert.equal(formatDate(null), "");
});

test("status helpers", () => {
  assert.equal(statusTone("approved"), "ok");
  assert.equal(statusTone("failed"), "err");
  assert.equal(statusTone("needs_review"), "warn");
  assert.equal(statusTone("unknown"), "neutral");
  assert.ok(isInProgress("processing"));
  assert.ok(!isInProgress("needs_review"));
});
