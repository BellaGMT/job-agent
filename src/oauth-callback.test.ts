import { test } from "node:test";
import assert from "node:assert/strict";
import { startCallbackServer } from "./oauth-callback.js";

async function withServer(fn: (callbackServer: Awaited<ReturnType<typeof startCallbackServer>>) => Promise<void>) {
  const callbackServer = await startCallbackServer();
  try {
    await fn(callbackServer);
  } finally {
    callbackServer.close();
  }
}

test("binds to loopback only, never to all interfaces", async () => {
  await withServer(async (callbackServer) => {
    assert.equal(callbackServer.host, "127.0.0.1");
    assert.match(callbackServer.redirectUri, /^http:\/\/127\.0\.0\.1:\d+$/);
  });
});

test("resolves with the code when state matches; ignores unrelated requests", async () => {
  await withServer(async (callbackServer) => {
    const code = callbackServer.waitForCode("st", 5000);
    assert.equal((await fetch(`${callbackServer.redirectUri}/favicon.ico`)).status, 404);
    const response = await fetch(`${callbackServer.redirectUri}/?state=st&code=abc`);
    assert.equal(response.status, 200);
    assert.equal(await code, "abc");
  });
});

test("rejects a forged callback with the wrong state", async () => {
  await withServer(async (callbackServer) => {
    const rejected = assert.rejects(callbackServer.waitForCode("st", 5000), /State mismatch/); // handler attached before the request
    const response = await fetch(`${callbackServer.redirectUri}/?state=other&code=abc`);
    assert.equal(response.status, 400);
    await rejected;
  });
});

test("reports a Google error without echoing it into the page", async () => {
  await withServer(async (callbackServer) => {
    const rejected = assert.rejects(callbackServer.waitForCode("st", 5000), /Google returned an error/);
    const response = await fetch(`${callbackServer.redirectUri}/?error=%3Cscript%3Ealert(1)%3C%2Fscript%3E`);
    assert.equal(response.headers.get("content-type"), "text/plain; charset=utf-8");
    assert.ok(!(await response.text()).includes("<script>"));
    await rejected;
  });
});

test("times out if the user never finishes signing in", async () => {
  await withServer(async (callbackServer) => {
    await assert.rejects(callbackServer.waitForCode("st", 20), /Timed out/);
  });
});
