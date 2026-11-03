// Full sync of one inbox's recent mail into the store.
//
// The Gmail API sits behind `MailSource` so the sync logic can be tested with
// a fake, and so API responses are validated in one place (`gmailSource`).
import type { gmail_v1 } from "googleapis";
import type { MessageStore } from "../store.js";
import { runPool } from "../pool.js";
import { Pacer } from "./pacer.js";
import { MalformedMessageError, parseMessage, type ParsedMessage } from "./parse.js";
import { statusOf, Throttle, withRetry } from "./retry.js";

export interface MailSource {
  /** Current position in the mailbox's change log. */
  historyId(): Promise<string>;
  listIds(query: string, pageToken?: string): Promise<{ ids: string[]; nextPageToken?: string }>;
  getMessage(id: string): Promise<gmail_v1.Schema$Message>;
}

export interface SyncOptions {
  months: number;
  ratePerSecond: number;
  concurrency: number;
  log: (line: string) => void;
}

export interface SyncResult {
  inWindow: number;
  stored: number;
  /** Listed, then deleted before we fetched them (404). */
  vanished: number;
  /** Ids the API returned without required fields. Not stored, so the next full sync tries them again. */
  malformed: string[];
}

/**
 * Fetch every message in the window that isn't stored yet.
 *
 * Restart-safe: stored ids are skipped and inserts are idempotent. The sync
 * cursor is captured BEFORE listing (mail arriving mid-sync lands after it)
 * and saved only AFTER every message is handled, so an interrupted sync never
 * marks itself complete.
 *
 * Permanent per-message problems (deleted mid-sync, malformed) are counted and
 * reported instead of aborting: otherwise one bad message would stall the
 * inbox forever. Anything else (network, auth, database) aborts the sync.
 */
export async function syncAccount(source: MailSource, store: MessageStore, email: string, opts: SyncOptions): Promise<SyncResult> {
  const pace = new Pacer(opts.ratePerSecond);
  const retry = retrier(pace, opts.log);
  store.ensureAccount(email);

  const cursor = await retry(() => source.historyId());
  const ids = await listAll(source, `newer_than:${opts.months}m -in:chats`, retry);
  const stored = store.storedIds(email);
  const todo = ids.filter((id) => !stored.has(id));
  opts.log(`${email}: ${ids.length} messages in window, ${todo.length} new`);

  const result: SyncResult = { inWindow: ids.length, stored: 0, vanished: 0, malformed: [] };
  let handled = 0;
  await runPool(todo, opts.concurrency, async (id) => {
    await pace.next();
    const msg = await fetchUnlessDeleted(source, id, retry);
    const parsed = msg ? parseOrReport(id, msg, result, opts.log) : undefined;
    if (!msg) result.vanished++;
    if (parsed) {
      store.insert(email, parsed);
      result.stored++;
    }
    if (++handled % 100 === 0 || handled === todo.length) opts.log(`  ${handled}/${todo.length}`);
  });

  store.completeSync(email, cursor);
  return result;
}

function parseOrReport(id: string, msg: gmail_v1.Schema$Message, result: SyncResult, log: (line: string) => void): ParsedMessage | undefined {
  try {
    return parseMessage(msg);
  } catch (err) {
    if (!(err instanceof MalformedMessageError)) throw err;
    result.malformed.push(id);
    log(`  skipped ${err.message}`);
    return undefined;
  }
}

type Retry = <T>(fn: () => Promise<T>) => Promise<T>;

/** One throttle per inbox (Gmail quotas are per user); a quota error also slows the pacer. */
function retrier(pace: Pacer, log: (line: string) => void): Retry {
  const throttle = new Throttle();
  return (fn) =>
    withRetry(fn, {
      throttle,
      onRateLimit: (ms) => {
        pace.slowDown();
        log(`  quota limit hit; pausing ${Math.round(ms / 1000)}s, then ${pace.perSecond.toFixed(1)} req/s`);
      },
    });
}

async function listAll(source: MailSource, query: string, retry: Retry): Promise<string[]> {
  const ids: string[] = [];
  let pageToken: string | undefined;
  do {
    const page = await retry(() => source.listIds(query, pageToken));
    ids.push(...page.ids);
    pageToken = page.nextPageToken;
  } while (pageToken);
  return ids;
}

/** A message deleted between listing and fetching returns 404: expected, not an error. */
async function fetchUnlessDeleted(source: MailSource, id: string, retry: Retry): Promise<gmail_v1.Schema$Message | undefined> {
  try {
    return await retry(() => source.getMessage(id));
  } catch (err) {
    if (statusOf(err) === 404) return undefined;
    throw err;
  }
}

/** Adapt the Gmail API client to `MailSource`, validating the fields the sync relies on. */
export function gmailSource(gmail: gmail_v1.Gmail): MailSource {
  return {
    async historyId() {
      const { data } = await gmail.users.getProfile({ userId: "me" });
      if (!data.historyId) throw new Error("Gmail profile has no historyId.");
      return data.historyId;
    },
    async listIds(q, pageToken) {
      const { data } = await gmail.users.messages.list({ userId: "me", q, maxResults: 500, pageToken });
      const ids = (data.messages ?? []).map((m) => m.id).filter((id): id is string => typeof id === "string" && id.length > 0);
      return data.nextPageToken ? { ids, nextPageToken: data.nextPageToken } : { ids };
    },
    async getMessage(id) {
      const { data } = await gmail.users.messages.get({ userId: "me", id, format: "full" });
      return data;
    },
  };
}
