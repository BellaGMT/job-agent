// Full sync of one inbox's recent mail into the store.
//
// The Gmail API sits behind `MailSource` so the sync logic can be tested with
// a fake, and so API responses are validated in one place (`gmailSource`).
import type { gmail_v1 } from "googleapis";
import { runPool } from "../pool.js";
import type { MessageStore } from "../store.js";
import { Pacer } from "./pacer.js";
import { MalformedMessageError, type ParsedMessage, parseMessage } from "./parse.js";
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
  /** Ids the API returned without required fields. Not stored; the next full sync tries them again. */
  malformed: string[];
  /** False when anything was skipped as malformed: the cursor then stays where it was. */
  completed: boolean;
}

/**
 * Fetch every message in the window that isn't stored yet.
 *
 * Restart-safe: stored ids are skipped and inserts are idempotent. The sync
 * cursor is captured BEFORE listing (mail arriving mid-sync lands after it)
 * and saved only AFTER every message is handled, so an interrupted sync never
 * marks itself complete.
 *
 * Per-message problems (deleted mid-sync, malformed) are counted and reported
 * instead of aborting, so one bad message doesn't block the rest. A deleted
 * message no longer exists, so the sync can still complete; a malformed one
 * wasn't stored, so the cursor is NOT advanced and the next full sync retries
 * it. Anything else (network, auth, database) aborts the sync.
 */
export async function syncAccount(
  source: MailSource,
  store: MessageStore,
  email: string,
  options: SyncOptions,
): Promise<SyncResult> {
  const pace = new Pacer(options.ratePerSecond);
  const retry = retrier(pace, options.log);
  store.ensureAccount(email);

  const cursor = await retry(() => source.historyId());
  const listedIds = await listAllIds(source, `newer_than:${options.months}m -in:chats`, retry);
  const existingMessageIds = store.storedIds(email);
  const pendingMessageIds = listedIds.filter((id) => !existingMessageIds.has(id));
  options.log(`${email}: ${listedIds.length} messages in window, ${pendingMessageIds.length} new`);

  const result: SyncResult = { inWindow: listedIds.length, stored: 0, vanished: 0, malformed: [], completed: false };
  let handledCount = 0;
  await runPool(pendingMessageIds, options.concurrency, async (id) => {
    await pace.next();
    const message = await fetchUnlessDeleted(source, id, retry);
    const parsedMessage = message ? parseOrReport(id, message, result, options.log) : undefined;
    if (!message) result.vanished++;
    if (parsedMessage) {
      store.insert(email, parsedMessage);
      result.stored++;
    }
    if (++handledCount % 100 === 0 || handledCount === pendingMessageIds.length) {
      options.log(`  ${handledCount}/${pendingMessageIds.length}`);
    }
  });

  if (result.malformed.length === 0) {
    store.completeSync(email, cursor);
    result.completed = true;
  }
  return result;
}

function parseOrReport(
  id: string,
  message: gmail_v1.Schema$Message,
  result: SyncResult,
  log: (line: string) => void,
): ParsedMessage | undefined {
  try {
    return parseMessage(message);
  } catch (err) {
    if (!(err instanceof MalformedMessageError)) throw err;
    result.malformed.push(id);
    log(`  skipped ${err.message}`);
    return undefined;
  }
}

type Retry = <T>(operation: () => Promise<T>) => Promise<T>;

/** One throttle per inbox (Gmail quotas are per user); a quota error also slows the pacer. */
function retrier(pace: Pacer, log: (line: string) => void): Retry {
  const throttle = new Throttle();
  return (operation) =>
    withRetry(operation, {
      throttle,
      onRateLimit: (ms) => {
        pace.slowDown();
        log(`  quota limit hit; pausing ${Math.round(ms / 1000)}s, then ${pace.perSecond.toFixed(1)} req/s`);
      },
    });
}

async function listAllIds(source: MailSource, query: string, retry: Retry): Promise<string[]> {
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
async function fetchUnlessDeleted(
  source: MailSource,
  id: string,
  retry: Retry,
): Promise<gmail_v1.Schema$Message | undefined> {
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
    async listIds(query, pageToken) {
      const { data } = await gmail.users.messages.list({ userId: "me", q: query, maxResults: 500, pageToken });
      const ids = (data.messages ?? [])
        .map((listed) => listed.id)
        .filter((id): id is string => typeof id === "string" && id.length > 0);
      return data.nextPageToken ? { ids, nextPageToken: data.nextPageToken } : { ids };
    },
    async getMessage(id) {
      const { data } = await gmail.users.messages.get({ userId: "me", id, format: "full" });
      return data;
    },
  };
}
