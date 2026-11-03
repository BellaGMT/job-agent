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
  const clock = fakeClock();
  const pacer = new Pacer(10, clock.now, clock.sleep);
  for (let request = 0; request < 3; request++) await pacer.next();
  assert.deepEqual(clock.wakeAt, [100, 200]);
});

test("concurrent callers get distinct slots", async () => {
  const clock = fakeClock();
  const pacer = new Pacer(4, clock.now, clock.sleep);
  await Promise.all([pacer.next(), pacer.next(), pacer.next()]);
  assert.deepEqual(clock.wakeAt, [250, 500]);
});

test("slowDown halves the rate down to a floor", () => {
  const pacer = new Pacer(3);
  pacer.slowDown();
  assert.equal(pacer.perSecond, 1.5);
  for (let halving = 0; halving < 10; halving++) pacer.slowDown();
  assert.equal(pacer.perSecond, Pacer.MIN_PER_SECOND);
});

test("rejects a non-positive rate", () => {
  assert.throws(() => new Pacer(0), /positive/);
  assert.throws(() => new Pacer(Number.NaN), /positive/);
});
