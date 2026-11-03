import assert from "node:assert/strict";
import { test } from "node:test";
import { runPool } from "./pool.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));

test("processes every item exactly once with bounded concurrency", async () => {
  const seen: number[] = [];
  let inFlight = 0;
  let peak = 0;
  await runPool([1, 2, 3, 4, 5, 6, 7], 3, async (item) => {
    inFlight++;
    peak = Math.max(peak, inFlight);
    await tick();
    seen.push(item);
    inFlight--;
  });
  assert.deepEqual(seen.sort(), [1, 2, 3, 4, 5, 6, 7]);
  assert.equal(peak, 3);
});

test("stops taking new items after a failure and rethrows the first error", async () => {
  const started: number[] = [];
  await assert.rejects(
    runPool([1, 2, 3, 4, 5, 6], 2, async (item) => {
      started.push(item);
      await tick();
      if (item === 1) throw new Error("first");
      if (item === 2) throw new Error("second");
    }),
    /first/,
  );
  assert.deepEqual(started, [1, 2]);
});

test("empty input and pool larger than input both work", async () => {
  await runPool([], 4, async () => assert.fail("must not run"));
  const seen: string[] = [];
  await runPool(["x"], 10, async (item) => void seen.push(item));
  assert.deepEqual(seen, ["x"]);
});

test("rejects a pool size below 1", async () => {
  await assert.rejects(
    runPool([1], 0, async () => {}),
    /at least 1/,
  );
});
