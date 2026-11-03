import { test } from "node:test";
import assert from "node:assert/strict";
import { Pacer } from "./pacer.js";

function fakeClock() {
  let now = 0;
  const wakeAt: number[] = []; // absolute times callers resume
  return {
    now: () => now,
    sleep: async (ms: number) => {
      wakeAt.push(now + ms);
      now += ms;
    },
    wakeAt,
  };
}

test("spaces requests at the configured rate, first one immediately", async () => {
  const c = fakeClock();
  const pacer = new Pacer(10, c.now, c.sleep);
  for (let i = 0; i < 3; i++) await pacer.next();
  assert.deepEqual(c.wakeAt, [100, 200]);
});

test("concurrent callers get distinct slots", async () => {
  const c = fakeClock();
  const pacer = new Pacer(4, c.now, c.sleep);
  await Promise.all([pacer.next(), pacer.next(), pacer.next()]);
  assert.deepEqual(c.wakeAt, [250, 500]);
});

test("slowDown halves the rate down to a floor", () => {
  const pacer = new Pacer(3);
  pacer.slowDown();
  assert.equal(pacer.perSecond, 1.5);
  for (let i = 0; i < 10; i++) pacer.slowDown();
  assert.equal(pacer.perSecond, Pacer.MIN_PER_SECOND);
});

test("rejects a non-positive rate", () => {
  assert.throws(() => new Pacer(0), /positive/);
  assert.throws(() => new Pacer(Number.NaN), /positive/);
});
