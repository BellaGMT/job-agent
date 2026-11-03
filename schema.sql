-- Schema for data/agent.db, the only interface between the TypeScript sync
-- (src/) and the Python labeling and eval tools (py/). Both sides apply this
-- file on startup; every statement must stay idempotent (IF NOT EXISTS).

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

-- The eval set: which emails are being labeled, in what order, and how each
-- was chosen ('keyword' and 'random' are sampled; 'targeted' are hand-picked).
CREATE TABLE IF NOT EXISTS label_queue (
  position INTEGER PRIMARY KEY,
  account  TEXT NOT NULL,
  gmail_id TEXT NOT NULL,
  bucket   TEXT NOT NULL CHECK (bucket IN ('keyword', 'random', 'targeted')),
  UNIQUE (account, gmail_id)
);
