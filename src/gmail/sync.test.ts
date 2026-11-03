import assert from "node:assert/strict";
import { test } from "node:test";
import type { gmail_v1 } from "googleapis";
import { openDb } from "../db.js";
import { MessageStore } from "../store.js";
import { type MailSource, syncAccount } from "./sync.js";

const EMAIL = "me@example.com";
const OPTS = { months: 3, ratePerSecond: 1000, concurrency: 3, log: () => {} };

function message(id: string): gmail_v1.Schema$Message {
  return {
    id,
    threadId: `t-${id}`,
    internalDate: "1761600000000",
    payload: { mimeType: "text/plain", headers: [{ name: "Subject", value: `subject ${id}` }], body: { data: "" } },
  };
}

/** In-memory mailbox: pages of ids, plus hooks to make individual fetches fail. */
function fakeSource(ids: string[], opts: { pageSize?: number; failOn?: Map<string, unknown>; history?: string } = {}) {
  const fetched: string[] = [];
  const queries: string[] = [];
  const pageSize = opts.pageSize ?? 2;
  const source: MailSource = {
    historyId: async () => opts.history ?? "h-100",
    listIds: async (q, token) => {
      queries.push(q);
      const start = token ? Number(token) : 0;
      const end = start + pageSize;
      const page = ids.slice(start, end);
      return end < ids.length ? { ids: page, nextPageToken: String(end) } : { ids: page };
    },
    getMessage: async (id) => {
      fetched.push(id);
      const err = opts.failOn?.get(id);
      if (err) throw err;
      return message(id);
    },
  };
  return { source, fetched, queries };
}

test("stores every listed message across pages and records the cursor", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const { source, queries } = fakeSource(["a", "b", "c", "d", "e"]);
  const result = await syncAccount(source, store, EMAIL, OPTS);
  assert.deepEqual(result, { inWindow: 5, stored: 5, vanished: 0, malformed: [], completed: true });
  assert.deepEqual([...store.storedIds(EMAIL)].sort(), ["a", "b", "c", "d", "e"]);
  assert.equal(store.cursor(EMAIL), "h-100");
  assert.equal(queries[0], "newer_than:3m -in:chats");
});

test("a re-run fetches only what isn't stored yet", async () => {
  const store = new MessageStore(openDb(":memory:"));
  await syncAccount(fakeSource(["a", "b"]).source, store, EMAIL, OPTS);
  const second = fakeSource(["a", "b", "c"]);
  const result = await syncAccount(second.source, store, EMAIL, OPTS);
  assert.deepEqual(second.fetched, ["c"]);
  assert.equal(result.stored, 1);
});

test("an interrupted sync keeps what it stored but does not advance the cursor", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const boom = Object.assign(new Error("Permission denied"), { status: 403 });
  const { source } = fakeSource(["a", "b", "c", "d"], { failOn: new Map([["c", boom]]) });
  await assert.rejects(syncAccount(source, store, EMAIL, OPTS), /Permission denied/);
  assert.equal(store.cursor(EMAIL), undefined);
  assert.ok(store.storedIds(EMAIL).size >= 1);
  assert.ok(!store.storedIds(EMAIL).has("c"));

  // Recovery: the next run completes and only then records the cursor.
  const retryRun = fakeSource(["a", "b", "c", "d"], { history: "h-200" });
  await syncAccount(retryRun.source, store, EMAIL, OPTS);
  assert.equal(store.cursor(EMAIL), "h-200");
  assert.equal(store.storedIds(EMAIL).size, 4);
});

test("messages deleted between listing and fetching (404) are skipped, not fatal", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const gone = Object.assign(new Error("Not Found"), { status: 404 });
  const { source } = fakeSource(["a", "b"], { failOn: new Map([["b", gone]]) });
  const result = await syncAccount(source, store, EMAIL, OPTS);
  assert.deepEqual(result, { inWindow: 2, stored: 1, vanished: 1, malformed: [], completed: true });
  assert.equal(store.cursor(EMAIL), "h-100");
});

test("a malformed message is skipped and reported, and the cursor stays put until it's stored", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const lines: string[] = [];
  const { source } = fakeSource(["a", "b"]);
  const real = source.getMessage;
  source.getMessage = async (id) => (id === "a" ? { id: "a" } : real(id)); // "a" has no threadId or date
  const result = await syncAccount(source, store, EMAIL, { ...OPTS, log: (line) => lines.push(line) });
  assert.deepEqual(result.malformed, ["a"]);
  assert.equal(result.completed, false);
  assert.equal(store.cursor(EMAIL), undefined);
  assert.deepEqual([...store.storedIds(EMAIL)], ["b"]);
  assert.ok(lines.some((line) => line.includes("message a is missing")));

  // Not stored, so the next full sync tries it again; once it's stored the cursor advances.
  const again = fakeSource(["a", "b"], { history: "h-200" });
  const secondResult = await syncAccount(again.source, store, EMAIL, OPTS);
  assert.deepEqual(again.fetched, ["a"]);
  assert.equal(secondResult.completed, true);
  assert.equal(store.cursor(EMAIL), "h-200");
});

test("a database failure aborts the sync and leaves the cursor alone", async () => {
  const db = openDb(":memory:");
  const store = new MessageStore(db);
  store.insert = () => {
    throw new Error("disk I/O error");
  };
  await assert.rejects(syncAccount(fakeSource(["a"]).source, store, EMAIL, OPTS), /disk I\/O error/);
  assert.equal(store.cursor(EMAIL), undefined);
});

test("an empty window still completes and records the cursor", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const result = await syncAccount(fakeSource([]).source, store, EMAIL, OPTS);
  assert.deepEqual(result, { inWindow: 0, stored: 0, vanished: 0, malformed: [], completed: true });
  assert.equal(store.cursor(EMAIL), "h-100");
});
