import { test } from "node:test";
import assert from "node:assert/strict";
import { runPool } from "./pool.js";

const tick = () => new Promise((r) => setImmediate(r));

test("processes every item exactly once with bounded concurrency", async () => {
  const seen: number[] = [];
  let inFlight = 0;
  let peak = 0;
  await runPool([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
    inFlight++;
    peak = Math.max(peak, inFlight);
    await tick();
    seen.push(n);
    inFlight--;
  });
  assert.deepEqual(seen.sort(), [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(peak, 3);
});

test("stops taking new items after a failure and rethrows the first error", async () => {
  const started: number[] = [];
  await assert.rejects(
    runPool([1, 2, 3, 4, 5, 6], 2, async (n) => {
      started.push(n);
      await tick();
      if (n === 1) throw new Error("first");
      if (n === 2) throw new Error("second");
    }),
    /first/,
  );
  assert.deepEqual(started, [1, 2]);
});

test("empty input and pool larger than input both work", async () => {
  await runPool([], 4, async () => assert.fail("must not run"));
  const seen: string[] = [];
  await runPool(["x"], 10, async (s) => void seen.push(s));
  assert.deepEqual(seen, ["x"]);
});

test("rejects a pool size below 1", async () => {
  await assert.rejects(runPool([1], 0, async () => {}), /at least 1/);
});
