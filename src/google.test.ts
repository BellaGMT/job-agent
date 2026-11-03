import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { isRevokedToken, listAccounts, loadToken, parseClientSecrets, parseCredentials, saveToken } from "./google.js";

const tempDir = () => fs.mkdtempSync(path.join(os.tmpdir(), "job-agent-tokens-"));

test("client secrets: accepts installed or web sections, rejects anything else", () => {
  assert.deepEqual(parseClientSecrets({ installed: { client_id: "id", client_secret: "s" } }), {
    clientId: "id",
    clientSecret: "s",
  });
  assert.equal(parseClientSecrets({ web: { client_id: "id", client_secret: "s" } }).clientId, "id");
  for (const bad of [null, [], {}, { installed: { client_id: "id" } }, { installed: { client_id: 1, client_secret: "s" } }]) {
    assert.throws(() => parseClientSecrets(bad), /doesn't look like an OAuth client file/);
  }
});

test("credentials: keeps known fields, drops unknown ones, requires a token", () => {
  assert.deepEqual(parseCredentials({ access_token: "a", expiry_date: 5, junk: { x: 1 } }), {
    access_token: "a",
    expiry_date: 5,
  });
  assert.throws(() => parseCredentials({ expiry_date: 5 }), /no access or refresh token/);
  assert.throws(() => parseCredentials("token"), /not an object/);
});

test("saveToken: a refresh without refresh_token keeps the stored one", () => {
  const dir = tempDir();
  saveToken("me@gmail.com", { access_token: "a1", refresh_token: "r1" }, dir);
  saveToken("me@gmail.com", { access_token: "a2", refresh_token: null }, dir);
  assert.deepEqual(loadToken("me@gmail.com", dir), { access_token: "a2", refresh_token: "r1" });
  assert.deepEqual(fs.readdirSync(dir), ["me@gmail.com.json"]); // no temp files left behind
});

test("token files can't be written outside the token directory", () => {
  const dir = tempDir();
  assert.throws(() => saveToken("../evil", { access_token: "a" }, dir), /Not a valid inbox address/);
  assert.throws(() => loadToken("..\\evil@x.com", dir), /Not a valid inbox address/);
});

test("listAccounts lists only token files named like inboxes", () => {
  const dir = tempDir();
  saveToken("b@gmail.com", { access_token: "a" }, dir);
  saveToken("a@gmail.com", { access_token: "a" }, dir);
  fs.writeFileSync(path.join(dir, "notes.json"), "{}");
  assert.deepEqual(listAccounts(dir), ["a@gmail.com", "b@gmail.com"]);
  assert.deepEqual(listAccounts(path.join(dir, "missing")), []);
});

test("isRevokedToken recognizes invalid_grant only", () => {
  assert.equal(isRevokedToken({ response: { data: { error: "invalid_grant" } } }), true);
  assert.equal(isRevokedToken(new Error("invalid_grant: Token has been expired or revoked.")), true);
  assert.equal(isRevokedToken({ status: 403, message: "Quota exceeded" }), false);
  assert.equal(isRevokedToken(null), false);
});
