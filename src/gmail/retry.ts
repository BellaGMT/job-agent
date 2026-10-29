// Retry policy for Gmail API calls.
//
// Gmail signals quota exhaustion two ways: 429, or 403 with a rate-limit reason
// ("Quota exceeded for quota metric ... Units per minute per user"). A 403 for
// anything else (revoked access, missing scope) must NOT be retried.
//
// Quotas are per user, so when one worker hits the limit, every worker on that
// inbox must back off together; otherwise the others keep burning the quota and
// the window never recovers. `Throttle` is that shared pause.

const RATE_LIMIT_REASONS = new Set(["rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"]);

interface ApiError {
  status?: number;
  code?: number | string;
  message?: string;
  response?: { headers?: unknown; data?: { error?: { errors?: { reason?: string }[] } } };
}

export function statusOf(err: unknown): number | undefined {
  const e = err as ApiError;
  const s = e?.status ?? e?.code;
  return typeof s === "number" ? s : undefined;
}

export function isRateLimit(err: unknown): boolean {
  const e = err as ApiError;
  const status = statusOf(err);
  if (status === 429) return true;
  if (status !== 403) return false;
  const reasons = e.response?.data?.error?.errors?.map((x) => x.reason) ?? [];
  return reasons.some((r) => r && RATE_LIMIT_REASONS.has(r)) || /quota exceeded|rate limit/i.test(e.message ?? "");
}

export function isRetryable(err: unknown): boolean {
  const status = statusOf(err);
  return isRateLimit(err) || (status !== undefined && status >= 500);
}

/** Seconds from a Retry-After header, if the server sent one. */
export function retryAfterMs(err: unknown): number | undefined {
  const headers = (err as ApiError).response?.headers as
    | { get?: (k: string) => string | null }
    | Record<string, string>
    | undefined;
  const raw =
    typeof (headers as { get?: unknown })?.get === "function"
      ? (headers as { get: (k: string) => string | null }).get("retry-after")
      : (headers as Record<string, string> | undefined)?.["retry-after"];
  const secs = raw ? Number(raw) : NaN;
  return Number.isFinite(secs) ? secs * 1000 : undefined;
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

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

export interface RetryOptions {
  attempts?: number;
  throttle?: Throttle;
  sleep?: (ms: number) => Promise<void>;
  onRateLimit?: (waitMs: number) => void;
}

export async function withRetry<T>(fn: () => Promise<T>, opts: RetryOptions = {}): Promise<T> {
  const { attempts = 8, throttle = new Throttle(), sleep = defaultSleep, onRateLimit } = opts;
  for (let i = 0; ; i++) {
    await throttle.wait(sleep);
    try {
      return await fn();
    } catch (err) {
      if (!isRetryable(err) || i >= attempts - 1) throw err;
      // Per-minute quota: back off on a scale of seconds to a minute, not milliseconds.
      const backoff = Math.min(60_000, 2 ** i * 2_000) + Math.random() * 1_000;
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
