import { test } from "node:test";
import assert from "node:assert/strict";
import type { gmail_v1 } from "googleapis";
import { openDb } from "../db.js";
import { MessageStore } from "../store.js";
import { syncAccount, type MailSource } from "./sync.js";

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
  const r = await syncAccount(source, store, EMAIL, OPTS);
  assert.deepEqual(r, { inWindow: 5, fetched: 5, vanished: 0 });
  assert.deepEqual([...store.storedIds(EMAIL)].sort(), ["a", "b", "c", "d", "e"]);
  assert.equal(store.cursor(EMAIL), "h-100");
  assert.equal(queries[0], "newer_than:3m -in:chats");
});

test("a re-run fetches only what isn't stored yet", async () => {
  const store = new MessageStore(openDb(":memory:"));
  await syncAccount(fakeSource(["a", "b"]).source, store, EMAIL, OPTS);
  const second = fakeSource(["a", "b", "c"]);
  const r = await syncAccount(second.source, store, EMAIL, OPTS);
  assert.deepEqual(second.fetched, ["c"]);
  assert.equal(r.fetched, 1);
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
  const r = await syncAccount(source, store, EMAIL, OPTS);
  assert.deepEqual(r, { inWindow: 2, fetched: 1, vanished: 1 });
  assert.equal(store.cursor(EMAIL), "h-100");
});

test("a malformed message from the API fails the sync instead of storing a bad row", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const { source } = fakeSource(["a"]);
  source.getMessage = async () => ({ id: "a" }); // no threadId, no date
  await assert.rejects(syncAccount(source, store, EMAIL, OPTS), /missing its id, thread id or date/);
  assert.equal(store.storedIds(EMAIL).size, 0);
});

test("an empty window still completes and records the cursor", async () => {
  const store = new MessageStore(openDb(":memory:"));
  const r = await syncAccount(fakeSource([]).source, store, EMAIL, OPTS);
  assert.deepEqual(r, { inWindow: 0, fetched: 0, vanished: 0 });
  assert.equal(store.cursor(EMAIL), "h-100");
});
