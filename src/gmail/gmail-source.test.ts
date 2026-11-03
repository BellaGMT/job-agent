import { test } from "node:test";
import assert from "node:assert/strict";
import type { gmail_v1 } from "googleapis";
import { gmailSource } from "./sync.js";

/**
 * A stand-in for the three Gmail endpoints the adapter calls. It records the
 * parameters each call receives. The real client type is huge, so the fake is
 * cast to it once here; the adapter's own logic (what it sends, how it
 * validates and shapes responses) is what's under test.
 */
function fakeGmail(responses: { profile?: object; list?: object; get?: object }) {
  const calls: { endpoint: string; params: unknown }[] = [];
  const record = (endpoint: string, data: object | undefined) => async (params: unknown) => {
    calls.push({ endpoint, params });
    return { data: data ?? {} };
  };
  const api = {
    users: {
      getProfile: record("getProfile", responses.profile),
      messages: { list: record("list", responses.list), get: record("get", responses.get) },
    },
  };
  return { gmail: api as unknown as gmail_v1.Gmail, calls };
}

test("historyId is required", async () => {
  assert.equal(await gmailSource(fakeGmail({ profile: { historyId: "42" } }).gmail).historyId(), "42");
  await assert.rejects(gmailSource(fakeGmail({ profile: {} }).gmail).historyId(), /no historyId/);
});

test("listIds forwards query and page token, drops missing or empty ids, returns the next token", async () => {
  const { gmail, calls } = fakeGmail({
    list: { messages: [{ id: "a" }, {}, { id: "" }, { id: "b" }], nextPageToken: "p3" },
  });
  assert.deepEqual(await gmailSource(gmail).listIds("newer_than:3m", "p2"), { ids: ["a", "b"], nextPageToken: "p3" });
  assert.deepEqual(calls, [
    { endpoint: "list", params: { userId: "me", q: "newer_than:3m", maxResults: 500, pageToken: "p2" } },
  ]);
});

test("a last page has no next token and an empty mailbox has no messages field", async () => {
  assert.deepEqual(await gmailSource(fakeGmail({ list: {} }).gmail).listIds("q"), { ids: [] });
});

test("getMessage requests the full message by id", async () => {
  const { gmail, calls } = fakeGmail({ get: { id: "m1", threadId: "t1" } });
  assert.deepEqual(await gmailSource(gmail).getMessage("m1"), { id: "m1", threadId: "t1" });
  assert.deepEqual(calls, [{ endpoint: "get", params: { userId: "me", id: "m1", format: "full" } }]);
});
