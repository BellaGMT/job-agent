// Retry policy for Gmail API calls.
//
// Gmail signals quota exhaustion two ways: 429, or 403 with a rate-limit reason
// ("Quota exceeded for quota metric ... Units per minute per user"). A 403 for
// anything else (revoked access, missing scope) must NOT be retried.
//
// Quotas are per user, so when one worker hits the limit, every worker on that
// inbox must back off together; otherwise the others keep burning the quota and
// the window never recovers. `Throttle` is that shared pause.

import { isRecord, stringField } from "../guards.js";

const RATE_LIMIT_REASONS = new Set(["rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"]);

export function statusOf(err: unknown): number | undefined {
  if (!isRecord(err)) return undefined;
  const status = err["status"] ?? err["code"];
  return typeof status === "number" ? status : undefined;
}

/** `reason` values from a Google API error body: response.data.error.errors[].reason */
function errorReasons(err: unknown): string[] {
  const response = isRecord(err) ? err["response"] : undefined;
  const data = isRecord(response) ? response["data"] : undefined;
  const error = isRecord(data) ? data["error"] : undefined;
  const errors = isRecord(error) ? error["errors"] : undefined;
  if (!Array.isArray(errors)) return [];
  return errors
    .map((entry: unknown) => stringField(entry, "reason"))
    .filter((reason): reason is string => reason !== undefined);
}

export function isRateLimit(err: unknown): boolean {
  const status = statusOf(err);
  if (status === 429) return true;
  if (status !== 403) return false;
  return (
    errorReasons(err).some((reason) => RATE_LIMIT_REASONS.has(reason)) ||
    /quota exceeded|rate limit/i.test(stringField(err, "message") ?? "")
  );
}

export function isRetryable(err: unknown): boolean {
  const status = statusOf(err);
  return isRateLimit(err) || (status !== undefined && status >= 500);
}

/**
 * Delay from a Retry-After header given in seconds, if the server sent one.
 * (The HTTP-date form is ignored; Google APIs send seconds.)
 */
export function retryAfterMs(err: unknown): number | undefined {
  const response = isRecord(err) ? err["response"] : undefined;
  const headers = isRecord(response) ? response["headers"] : undefined;
  const headerValue = headers instanceof Headers ? headers.get("retry-after") : stringField(headers, "retry-after");
  const seconds = headerValue ? Number(headerValue) : NaN;
  return Number.isFinite(seconds) && seconds >= 0 ? seconds * 1000 : undefined;
}

export class Throttle {
  private pausedUntil = 0;
  constructor(private readonly now: () => number = Date.now) {}

  pause(ms: number): void {
    this.pausedUntil = Math.max(this.pausedUntil, this.now() + ms);
  }

  /** Time left in the current pause, 0 if none. */
  remaining(): number {
    return Math.max(0, this.pausedUntil - this.now());
  }

  async wait(sleep = defaultSleep): Promise<void> {
    for (let ms = this.remaining(); ms > 0; ms = this.remaining()) await sleep(ms);
  }
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export interface RetryOptions {
  attempts?: number;
  throttle?: Throttle;
  sleep?: (ms: number) => Promise<void>;
  onRateLimit?: (waitMs: number) => void;
}

export async function withRetry<T>(operation: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const { attempts = 8, throttle = new Throttle(), sleep = defaultSleep, onRateLimit } = options;
  for (let attempt = 0; ; attempt++) {
    await throttle.wait(sleep);
    try {
      return await operation();
    } catch (err) {
      if (!isRetryable(err) || attempt >= attempts - 1) throw err;
      // Per-minute quota: back off on a scale of seconds to a minute, not milliseconds.
      const backoff = Math.min(60_000, 2 ** attempt * 2_000) + Math.random() * 1_000;
      const waitMs = retryAfterMs(err) ?? backoff;
      if (isRateLimit(err)) {
        throttle.pause(waitMs); // everyone waits, not just this worker
        onRateLimit?.(waitMs);
      } else {
        await sleep(waitMs);
      }
    }
  }
}
