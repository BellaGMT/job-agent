// Pull recent mail from every connected inbox into SQLite.
//   npm run ingest                 all inboxes, last 3 months
//   npm run ingest -- --months 6   wider window
//   npm run ingest -- --account you@gmail.com
//
// Resumable: messages already stored are skipped, so Ctrl-C and re-run is safe.
import { google, gmail_v1 } from "googleapis";
import { db } from "./db.js";
import { authorizedClient, isRevokedToken, listAccounts } from "./google.js";
import { parseMessage } from "./gmail/parse.js";

const args = process.argv.slice(2);
const arg = (name: string) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : undefined;
};
const MONTHS = Number(arg("months") ?? 3);
// Gmail allows ~250 quota units/user/second and messages.get costs 5, so ~50
// gets/s is the ceiling. 8 in flight stays well under it with room for retries.
const CONCURRENCY = 8;

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

  // Capture the history cursor BEFORE listing. Anything that arrives while we
  // sync is then covered by the next incremental sync instead of falling in a gap.
  const { data: profile } = await gmail.users.getProfile({ userId: "me" });

  const ids = await listMessageIds(gmail, `newer_than:${MONTHS}m -in:chats`);
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
    const { data } = await withRetry(() =>
      gmail.users.messages.get({ userId: "me", id, format: "full" }),
    );
    const m = parseMessage(data);
    insertMessage.run({ account: email, ...m, labelIds: JSON.stringify(m.labelIds) });
    if (++done % 100 === 0 || done === todo.length) console.log(`  ${done}/${todo.length}`);
  });

  db.prepare(
    "UPDATE accounts SET last_history_id = ?, last_synced_at = datetime('now') WHERE email = ?",
  ).run(profile.historyId, email);
}

async function listMessageIds(gmail: gmail_v1.Gmail, q: string): Promise<string[]> {
  const ids: string[] = [];
  let pageToken: string | undefined;
  do {
    const { data } = await withRetry(() =>
      gmail.users.messages.list({ userId: "me", q, maxResults: 500, pageToken }),
    );
    for (const m of data.messages ?? []) ids.push(m.id!);
    pageToken = data.nextPageToken ?? undefined;
  } while (pageToken);
  return ids;
}

// Retry only what's worth retrying: rate limits and server errors, with
// exponential backoff + jitter so 8 workers don't retry in lockstep.
async function withRetry<T>(fn: () => Promise<T>, attempts = 5): Promise<T> {
  for (let i = 0; ; i++) {
    try {
      return await fn();
    } catch (err) {
      const status = (err as { status?: number; code?: number }).status ?? (err as { code?: number }).code;
      const retryable = status === 429 || (typeof status === "number" && status >= 500);
      if (!retryable || i >= attempts - 1) throw err;
      const delay = 2 ** i * 500 + Math.random() * 250;
      await new Promise((r) => setTimeout(r, delay));
    }
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
  console.error(err);
  process.exit(1);
});
