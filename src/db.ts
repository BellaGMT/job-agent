// Opening and migrating the SQLite database shared by the TypeScript and Python sides.
import Database from "better-sqlite3";
import fs from "node:fs";
import path from "node:path";

// AGENT_DB lets tests point at a throwaway database instead of your real one.
export const DB_PATH = process.env["AGENT_DB"] ?? "data/agent.db";

const SCHEMA = `
CREATE TABLE IF NOT EXISTS accounts (
  email           TEXT PRIMARY KEY,
  added_at        TEXT NOT NULL DEFAULT (datetime('now')),
  -- Gmail's change cursor, captured at the START of a full sync, so incremental
  -- syncs fetch only what changed since instead of re-listing everything.
  last_history_id TEXT,
  last_synced_at  TEXT
);

-- Raw email, one row per (inbox, message). The same email can exist in two
-- inboxes (forwarded, CC'd); rfc_message_id dedupes across inboxes.
CREATE TABLE IF NOT EXISTS messages (
  account        TEXT NOT NULL REFERENCES accounts(email),
  gmail_id       TEXT NOT NULL,
  thread_id      TEXT NOT NULL,
  rfc_message_id TEXT,
  in_reply_to    TEXT,                  -- RFC Message-ID this replies to
  references_ids TEXT,                  -- RFC References header: the conversation chain,
                                        -- which works across inboxes (Gmail thread ids don't)
  internal_date  INTEGER NOT NULL,      -- ms since epoch, when Gmail received it
  from_addr      TEXT,
  to_addr        TEXT,
  cc_addr        TEXT,
  subject        TEXT,
  snippet        TEXT,
  body_text      TEXT,
  label_ids      TEXT,                  -- JSON array of Gmail label ids
  fetched_at     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (account, gmail_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_rfc  ON messages(rfc_message_id);
CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(internal_date);

-- Hand labels: ground truth for classification and linking evals.
CREATE TABLE IF NOT EXISTS labels (
  account         TEXT NOT NULL,
  gmail_id        TEXT NOT NULL,
  category        TEXT NOT NULL,
  application_key TEXT,                 -- "company | role", null if not recruiting
  notes           TEXT,
  labeled_at      TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (account, gmail_id),
  FOREIGN KEY (account, gmail_id) REFERENCES messages(account, gmail_id)
);
`;

/** Create missing tables and indexes. Safe to run on every start. */
export function migrate(db: Database.Database): void {
  db.exec(SCHEMA);
}

/** Open (creating if needed) and migrate the database. Pass ":memory:" in tests. */
export function openDb(file: string = DB_PATH): Database.Database {
  if (file !== ":memory:") fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new Database(file);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON"); // SQLite ignores REFERENCES clauses unless this is on
  migrate(db);
  return db;
}
