import assert from "node:assert/strict";
import { test } from "node:test";
import { openDb } from "./db.js";
import type { ParsedMessage } from "./gmail/parse.js";
import { MessageStore } from "./store.js";

const EMAIL = "me@example.com";

function parsedMessage(gmailId: string, subject = "Hello"): ParsedMessage {
  return {
    gmailId,
    threadId: `t-${gmailId}`,
    rfcMessageId: `<${gmailId}@example.com>`,
    inReplyTo: null,
    referencesIds: null,
    internalDate: 1_761_600_000_000,
    from: "a@example.com",
    to: EMAIL,
    cc: null,
    subject,
    snippet: null,
    bodyText: "body",
    labelIds: ["INBOX"],
  };
}

test("inserting the same message twice keeps the first copy and doesn't fail", () => {
  const store = new MessageStore(openDb(":memory:"));
  store.ensureAccount(EMAIL);
  store.insert(EMAIL, parsedMessage("m1", "first"));
  store.insert(EMAIL, parsedMessage("m1", "second"));
  assert.deepEqual([...store.storedIds(EMAIL)], ["m1"]);
  assert.deepEqual(store.countsByAccount(), [{ account: EMAIL, messages: 1 }]);
});

test("the same Gmail id in two inboxes is two rows", () => {
  const store = new MessageStore(openDb(":memory:"));
  for (const account of [EMAIL, "other@example.com"]) {
    store.ensureAccount(account);
    store.insert(account, parsedMessage("same-id"));
  }
  assert.equal(store.storedIds(EMAIL).size, 1);
  assert.equal(store.storedIds("other@example.com").size, 1);
});

test("messages for an unknown account are rejected by the foreign key", () => {
  const store = new MessageStore(openDb(":memory:"));
  assert.throws(() => store.insert("nobody@example.com", parsedMessage("m1")), /FOREIGN KEY/);
});

test("the cursor is unset until a sync completes", () => {
  const store = new MessageStore(openDb(":memory:"));
  store.ensureAccount(EMAIL);
  assert.equal(store.cursor(EMAIL), undefined);
  store.completeSync(EMAIL, "h-1");
  assert.equal(store.cursor(EMAIL), "h-1");
});

test("label ids are stored as JSON", () => {
  const db = openDb(":memory:");
  const store = new MessageStore(db);
  store.ensureAccount(EMAIL);
  store.insert(EMAIL, parsedMessage("m1"));
  const stored: unknown = db.prepare("SELECT label_ids FROM messages").pluck().get();
  assert.equal(stored, '["INBOX"]');
});

test("upsert overwrites parsed fields of an existing message and inserts new ones", () => {
  const db = openDb(":memory:");
  const store = new MessageStore(db);
  store.ensureAccount(EMAIL);
  store.insert(EMAIL, parsedMessage("m1", "old subject"));
  store.upsert(EMAIL, parsedMessage("m1", "new subject"));
  store.upsert(EMAIL, parsedMessage("m2", "brand new"));
  const subjects = db.prepare("SELECT subject FROM messages ORDER BY gmail_id").pluck().all();
  assert.deepEqual(subjects, ["new subject", "brand new"]);
});

test("insert, unlike upsert, never overwrites", () => {
  const db = openDb(":memory:");
  const store = new MessageStore(db);
  store.ensureAccount(EMAIL);
  store.upsert(EMAIL, parsedMessage("m1", "first"));
  store.insert(EMAIL, parsedMessage("m1", "second"));
  assert.equal(db.prepare("SELECT subject FROM messages").pluck().get(), "first");
});
