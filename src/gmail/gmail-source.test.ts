import { test } from "node:test";
import assert from "node:assert/strict";
import type { gmail_v1 } from "googleapis";
import { gmailSource } from "./sync.js";

/**
 * A stand-in for the three Gmail endpoints the adapter calls. The real client
 * type is huge, so the fake is cast to it once here; the adapter's own logic
 * (validation and shaping of responses) is what's under test.
 */
function fakeGmail(responses: { profile?: object; list?: object; get?: object }): gmail_v1.Gmail {
  const api = {
    users: {
      getProfile: async () => ({ data: responses.profile ?? {} }),
      messages: {
        list: async () => ({ data: responses.list ?? {} }),
        get: async () => ({ data: responses.get ?? {} }),
      },
    },
  };
  return api as unknown as gmail_v1.Gmail;
}

test("historyId is required", async () => {
  assert.equal(await gmailSource(fakeGmail({ profile: { historyId: "42" } })).historyId(), "42");
  await assert.rejects(gmailSource(fakeGmail({ profile: {} })).historyId(), /no historyId/);
});

test("list pages drop missing or empty ids and pass the page token through", async () => {
  const source = gmailSource(
    fakeGmail({ list: { messages: [{ id: "a" }, {}, { id: "" }, { id: "b" }], nextPageToken: "p2" } }),
  );
  assert.deepEqual(await source.listIds("q"), { ids: ["a", "b"], nextPageToken: "p2" });
});

test("a last page has no next token and an empty mailbox has no messages field", async () => {
  assert.deepEqual(await gmailSource(fakeGmail({ list: {} })).listIds("q"), { ids: [] });
});
