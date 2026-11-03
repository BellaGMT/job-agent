import { test } from "node:test";
import assert from "node:assert/strict";
import { startCallbackServer } from "./oauth-callback.js";

async function withServer(fn: (s: Awaited<ReturnType<typeof startCallbackServer>>) => Promise<void>) {
  const s = await startCallbackServer();
  try {
    await fn(s);
  } finally {
    s.close();
  }
}

test("listens on loopback only", async () => {
  await withServer(async (s) => assert.match(s.redirectUri, /^http:\/\/127\.0\.0\.1:\d+$/));
});

test("resolves with the code when state matches; ignores unrelated requests", async () => {
  await withServer(async (s) => {
    const code = s.waitForCode("st", 5000);
    assert.equal((await fetch(`${s.redirectUri}/favicon.ico`)).status, 404);
    const res = await fetch(`${s.redirectUri}/?state=st&code=abc`);
    assert.equal(res.status, 200);
    assert.equal(await code, "abc");
  });
});

test("rejects a forged callback with the wrong state", async () => {
  await withServer(async (s) => {
    const rejected = assert.rejects(s.waitForCode("st", 5000), /State mismatch/); // handler attached before the request
    const res = await fetch(`${s.redirectUri}/?state=other&code=abc`);
    assert.equal(res.status, 400);
    await rejected;
  });
});

test("reports a Google error without echoing it into the page", async () => {
  await withServer(async (s) => {
    const rejected = assert.rejects(s.waitForCode("st", 5000), /Google returned an error/);
    const res = await fetch(`${s.redirectUri}/?error=%3Cscript%3Ealert(1)%3C%2Fscript%3E`);
    assert.equal(res.headers.get("content-type"), "text/plain; charset=utf-8");
    assert.ok(!(await res.text()).includes("<script>"));
    await rejected;
  });
});

test("times out if the user never finishes signing in", async () => {
  await withServer(async (s) => {
    await assert.rejects(s.waitForCode("st", 20), /Timed out/);
  });
});
