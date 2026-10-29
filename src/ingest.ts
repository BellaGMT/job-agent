// Pull recent mail from every connected inbox into SQLite.
//   npm run ingest                 all inboxes, last 3 months
//   npm run ingest -- --months 6   wider window
//   npm run ingest -- --account you@gmail.com
//   npm run ingest -- --rate 5     slower, if your project's quota is low
//
// Resumable: messages already stored are skipped, so Ctrl-C and re-run is safe.
import { google, gmail_v1 } from "googleapis";
import { db } from "./db.js";
import { authorizedClient, isRevokedToken, listAccounts } from "./google.js";
import { parseMessage } from "./gmail/parse.js";
import { Throttle, withRetry, statusOf } from "./gmail/retry.js";

const args = process.argv.slice(2);
const arg = (name: string) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : undefined;
};
const MONTHS = Number(arg("months") ?? 3);
// Two limits on request rate:
//  - RATE paces request starts (messages.get costs 5 quota units, so 10/s = 3,000
//    units/min). Pacing avoids hitting the quota instead of recovering from it.
//  - CONCURRENCY caps requests in flight, which bounds latency-driven bursts.
// If Gmail still reports a quota error, every worker pauses (see gmail/retry.ts).
const RATE = Number(arg("rate") ?? 10);
const CONCURRENCY = 4;

const insertMessage = db.prepare(`
  INSERT OR IGNORE INTO messages
    (account, gmail_id, thread_id, rfc_message_id, in_reply_to, references_ids,
     internal_date, from_addr, to_addr, cc_addr, subject, snippet, body_text, label_ids)
  VALUES
    (@account, @gmailId, @threadId, @rfcMessageId, @inReplyTo, @referencesIds,
     @internalDate, @from, @to, @cc, @subject, @snippet, @bodyText, @labelIds)
`);

async function syncAccount(email: string) {
  const gmail = google.gmail({ version: "v1", auth: authorizedClient(email) });
  // One throttle per inbox: Gmail quotas are per user.
  const throttle = new Throttle();
  const pace = new Pacer(RATE);
  const retry = <T>(fn: () => Promise<T>) =>
    withRetry(fn, {
      throttle,
      onRateLimit: (ms) => {
        pace.slowDown();
        console.log(
          `  quota limit hit; pausing ${Math.round(ms / 1000)}s, then ${pace.perSecond.toFixed(1)} req/s`,
        );
      },
    });

  // Capture the history cursor BEFORE listing. Anything that arrives while we
  // sync is then covered by the next incremental sync instead of falling in a gap.
  const { data: profile } = await gmail.users.getProfile({ userId: "me" });

  const ids = await listMessageIds(gmail, `newer_than:${MONTHS}m -in:chats`, retry);
  const have = new Set(
    db
      .prepare("SELECT gmail_id FROM messages WHERE account = ?")
      .pluck()
      .all(email) as string[],
  );
  const todo = ids.filter((id) => !have.has(id));
  console.log(`${email}: ${ids.length} messages in window, ${todo.length} new`);

  let done = 0;
  await runPool(todo, CONCURRENCY, async (id) => {
    await pace.next();
    const { data } = await retry(() => gmail.users.messages.get({ userId: "me", id, format: "full" }));
    const m = parseMessage(data);
    insertMessage.run({ account: email, ...m, labelIds: JSON.stringify(m.labelIds) });
    if (++done % 100 === 0 || done === todo.length) console.log(`  ${done}/${todo.length}`);
  });

  db.prepare(
    "UPDATE accounts SET last_history_id = ?, last_synced_at = datetime('now') WHERE email = ?",
  ).run(profile.historyId, email);
}

async function listMessageIds(
  gmail: gmail_v1.Gmail,
  q: string,
  retry: <T>(fn: () => Promise<T>) => Promise<T>,
): Promise<string[]> {
  const ids: string[] = [];
  let pageToken: string | undefined;
  do {
    const { data } = await retry(() =>
      gmail.users.messages.list({ userId: "me", q, maxResults: 500, pageToken }),
    );
    for (const m of data.messages ?? []) ids.push(m.id!);
    pageToken = data.nextPageToken ?? undefined;
  } while (pageToken);
  return ids;
}

/**
 * Spaces out request starts across all workers. Halves its rate on every quota
 * error, so a sync settles under whatever the project's real limit turns out to be.
 */
class Pacer {
  private nextAt = 0;
  constructor(public perSecond: number) {}

  slowDown(): void {
    this.perSecond = Math.max(0.5, this.perSecond / 2);
  }

  async next(): Promise<void> {
    const now = Date.now();
    const at = Math.max(now, this.nextAt);
    this.nextAt = at + 1000 / this.perSecond;
    if (at > now) await new Promise((r) => setTimeout(r, at - now));
  }
}

async function runPool<T>(items: T[], size: number, worker: (item: T) => Promise<void>) {
  let next = 0;
  const lanes = Array.from({ length: Math.min(size, items.length) }, async () => {
    while (next < items.length) await worker(items[next++]);
  });
  await Promise.all(lanes);
}

async function main() {
  const only = arg("account");
  const accounts = only ? [only] : listAccounts();
  if (accounts.length === 0) throw new Error("No inboxes connected. Run: npm run auth");

  for (const email of accounts) {
    db.prepare("INSERT OR IGNORE INTO accounts (email) VALUES (?)").run(email);
    try {
      await syncAccount(email);
    } catch (err) {
      if (isRevokedToken(err)) {
        // One revoked inbox shouldn't block the others.
        console.error(`${email}: access expired or revoked. Run: npm run auth (pick ${email})`);
        process.exitCode = 1;
        continue;
      }
      throw err;
    }
  }

  const total = db.prepare("SELECT account, COUNT(*) n FROM messages GROUP BY account").all();
  console.table(total);
}

main().catch((err) => {
  // The full Gaxios error is a 100-line dump; status + message is what's actionable.
  const status = statusOf(err);
  console.error(`Sync failed${status ? ` (HTTP ${status})` : ""}: ${err?.message ?? err}`);
  console.error("Already-fetched messages are saved; re-run to continue.");
  process.exit(1);
});
