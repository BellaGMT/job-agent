import { test } from "node:test";
import assert from "node:assert/strict";
import { isRateLimit, isRetryable, retryAfterMs, Throttle, withRetry } from "./retry.js";

// Shape of the real error Gmail returned during a 3-month sync.
const quota403 = {
  status: 403,
  code: 403,
  message:
    "Quota exceeded for quota metric 'Total Query Cost' and limit 'Units per minute per user' of service 'gmail.googleapis.com'",
  response: { data: { error: { errors: [{ reason: "rateLimitExceeded" }] } } },
};
const forbidden403 = {
  status: 403,
  message: "Request had insufficient authentication scopes.",
  response: { data: { error: { errors: [{ reason: "insufficientPermissions" }] } } },
};

test("classifies Gmail errors", () => {
  assert.equal(isRateLimit(quota403), true);
  assert.equal(isRateLimit({ status: 403, message: "Quota exceeded for quota metric" }), true);
  assert.equal(isRateLimit({ status: 429 }), true);
  assert.equal(isRateLimit(forbidden403), false);
  assert.equal(isRetryable(forbidden403), false);
  assert.equal(isRetryable({ status: 503 }), true);
  assert.equal(isRetryable({ status: 404 }), false);
});

test("reads Retry-After from fetch-style and plain headers", () => {
  assert.equal(retryAfterMs({ response: { headers: new Headers({ "retry-after": "7" }) } }), 7000);
  assert.equal(retryAfterMs({ response: { headers: { "retry-after": "3" } } }), 3000);
  assert.equal(retryAfterMs({ response: { headers: new Headers() } }), undefined);
});

test("a rate limit pauses every caller sharing the throttle", async () => {
  let clock = 0;
  const throttle = new Throttle(() => clock);
  const sleeps: number[] = [];
  const sleep = async (ms: number) => {
    sleeps.push(ms);
    clock += ms;
  };

  let calls = 0;
  const result = await withRetry(
    async () => {
      if (++calls === 1) throw quota403;
      return "ok";
    },
    { throttle, sleep },
  );
  assert.equal(result, "ok");
  assert.equal(calls, 2);
  assert.ok(sleeps[0] >= 2000, `expected a seconds-scale backoff, got ${sleeps[0]}ms`);

  // A second worker arriving during a pause must wait it out too.
  throttle.pause(5000);
  const before = clock;
  await withRetry(async () => "ok", { throttle, sleep });
  assert.equal(clock - before, 5000);
});

test("non-retryable errors fail immediately", async () => {
  let calls = 0;
  await assert.rejects(
    withRetry(
      async () => {
        calls++;
        throw forbidden403;
      },
      { sleep: async () => {} },
    ),
  );
  assert.equal(calls, 1);
});

test("gives up after the attempt budget", async () => {
  let calls = 0;
  await assert.rejects(
    withRetry(
      async () => {
        calls++;
        throw { status: 500 };
      },
      { attempts: 3, sleep: async () => {} },
    ),
  );
  assert.equal(calls, 3);
});
