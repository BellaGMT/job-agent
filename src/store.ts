// Persistence for synced mail. All SQL for the sync lives here, parameterized.
import type Database from "better-sqlite3";
import type { ParsedMessage } from "./gmail/parse.js";
import { isRecord, stringField } from "./guards.js";

export class MessageStore {
  private readonly insertStatement: Database.Statement;
  private readonly upsertStatement: Database.Statement;

  constructor(private readonly db: Database.Database) {
    // INSERT OR IGNORE makes inserting idempotent: a re-run after an interruption
    // can't create duplicates or fail on rows it already wrote.
    this.insertStatement = db.prepare(`
      INSERT OR IGNORE INTO messages
        (account, gmail_id, thread_id, rfc_message_id, in_reply_to, references_ids,
         internal_date, from_addr, to_addr, cc_addr, subject, snippet, body_text, label_ids)
      VALUES
        (@account, @gmailId, @threadId, @rfcMessageId, @inReplyTo, @referencesIds,
         @internalDate, @from, @to, @cc, @subject, @snippet, @bodyText, @labelIds)`);
    // Updates in place rather than INSERT OR REPLACE: REPLACE deletes the old row
    // first, which the labels foreign key would reject (or, without it, orphan labels).
    this.upsertStatement = db.prepare(`
      INSERT INTO messages
        (account, gmail_id, thread_id, rfc_message_id, in_reply_to, references_ids,
         internal_date, from_addr, to_addr, cc_addr, subject, snippet, body_text, label_ids)
      VALUES
        (@account, @gmailId, @threadId, @rfcMessageId, @inReplyTo, @referencesIds,
         @internalDate, @from, @to, @cc, @subject, @snippet, @bodyText, @labelIds)
      ON CONFLICT (account, gmail_id) DO UPDATE SET
        thread_id = excluded.thread_id, rfc_message_id = excluded.rfc_message_id,
        in_reply_to = excluded.in_reply_to, references_ids = excluded.references_ids,
        internal_date = excluded.internal_date, from_addr = excluded.from_addr, to_addr = excluded.to_addr,
        cc_addr = excluded.cc_addr, subject = excluded.subject, snippet = excluded.snippet,
        body_text = excluded.body_text, label_ids = excluded.label_ids, fetched_at = datetime('now')`);
  }

  ensureAccount(email: string): void {
    this.db.prepare("INSERT OR IGNORE INTO accounts (email) VALUES (?)").run(email);
  }

  storedIds(email: string): Set<string> {
    const rows: unknown[] = this.db.prepare("SELECT gmail_id FROM messages WHERE account = ?").pluck().all(email);
    return new Set(rows.filter((gmailId): gmailId is string => typeof gmailId === "string"));
  }

  insert(email: string, message: ParsedMessage): void {
    this.insertStatement.run({ account: email, ...message, labelIds: JSON.stringify(message.labelIds) });
  }

  /** Insert, or overwrite the parsed fields of a message already stored. */
  upsert(email: string, message: ParsedMessage): void {
    this.upsertStatement.run({ account: email, ...message, labelIds: JSON.stringify(message.labelIds) });
  }

  /** Record a completed full sync. Call only after every message is stored. */
  completeSync(email: string, historyId: string): void {
    this.db
      .prepare("UPDATE accounts SET last_history_id = ?, last_synced_at = datetime('now') WHERE email = ?")
      .run(historyId, email);
  }

  cursor(email: string): string | undefined {
    const historyId: unknown = this.db
      .prepare("SELECT last_history_id FROM accounts WHERE email = ?")
      .pluck()
      .get(email);
    return typeof historyId === "string" ? historyId : undefined;
  }

  countsByAccount(): { account: string; messages: number }[] {
    const rows: unknown[] = this.db
      .prepare("SELECT account, COUNT(*) AS message_count FROM messages GROUP BY account")
      .all();
    return rows.flatMap((row) => {
      const account = stringField(row, "account");
      const messageCount = isRecord(row) ? row["message_count"] : undefined;
      return account && typeof messageCount === "number" ? [{ account, messages: messageCount }] : [];
    });
  }
}
