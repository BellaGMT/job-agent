import assert from "node:assert/strict";
import { test } from "node:test";
import { parseIngestArgs } from "./cli-args.js";

test("defaults", () => {
  assert.deepEqual(parseIngestArgs([]), { months: 3, ratePerSecond: 10, refresh: false });
});

test("all flags", () => {
  assert.deepEqual(parseIngestArgs(["--months", "6", "--rate", "2.5", "--account", "a.b@gmail.com"]), {
    months: 6,
    ratePerSecond: 2.5,
    refresh: false,
    account: "a.b@gmail.com",
  });
});

test("--refresh takes no value and can appear anywhere", () => {
  assert.equal(parseIngestArgs(["--refresh"]).refresh, true);
  assert.deepEqual(parseIngestArgs(["--months", "2", "--refresh", "--rate", "4"]), {
    months: 2,
    ratePerSecond: 4,
    refresh: true,
  });
});

test("rejects unknown flags, bare words and missing values", () => {
  assert.throws(() => parseIngestArgs(["--", "rate", "3"]), /Unexpected argument "--"/);
  assert.throws(() => parseIngestArgs(["--speed", "3"]), /Unexpected argument "--speed"/);
  assert.throws(() => parseIngestArgs(["--rate"]), /Unexpected argument "--rate"/);
});

test("rejects out-of-range and non-numeric values", () => {
  assert.throws(() => parseIngestArgs(["--months", "abc"]), /--months must be a whole number/);
  assert.throws(() => parseIngestArgs(["--months", "2.5"]), /--months/);
  assert.throws(() => parseIngestArgs(["--months", "0"]), /--months/);
  assert.throws(() => parseIngestArgs(["--rate", "0"]), /--rate/);
  assert.throws(() => parseIngestArgs(["--rate", "Infinity"]), /--rate/);
});

test("rejects account values that could escape the token directory", () => {
  assert.throws(() => parseIngestArgs(["--account", "../secrets"]), /Not a valid inbox address/);
});
