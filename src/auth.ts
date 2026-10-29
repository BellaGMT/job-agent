// Connect one Gmail inbox. Run once per inbox: npm run auth
//
// Flow (OAuth 2.0 for installed apps, "loopback" variant):
//   1. Start a tiny HTTP server on 127.0.0.1 at a random free port.
//   2. Send you to Google's consent page with that address as the redirect.
//   3. Google redirects back to the server with a one-time code.
//   4. Exchange code -> tokens (access + refresh), save them under tokens/.
// PKCE and `state` stop another local process from hijacking or forging step 3.
import http from "node:http";
import crypto from "node:crypto";
import type { AddressInfo } from "node:net";
import { google, Auth } from "googleapis";
import { db } from "./db.js";
import { newOAuthClient, saveToken, SCOPES } from "./google.js";

async function main() {
  const server = http.createServer();
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;

  const client = newOAuthClient(`http://127.0.0.1:${port}`);
  const state = crypto.randomBytes(16).toString("hex");
  const { codeVerifier, codeChallenge } = await client.generateCodeVerifierAsync();

  const url = client.generateAuthUrl({
    access_type: "offline", // ask for a refresh token
    prompt: "consent", // force one even if you've consented before
    scope: SCOPES,
    state,
    code_challenge: codeChallenge,
    code_challenge_method: Auth.CodeChallengeMethod.S256,
  });

  console.log("\nOpen this URL and pick the inbox to connect:\n\n" + url + "\n");

  const code = await new Promise<string>((resolve, reject) => {
    server.on("request", (req, res) => {
      const params = new URL(req.url ?? "/", `http://127.0.0.1:${port}`).searchParams;
      if (!params.has("code") && !params.has("error")) {
        res.writeHead(404).end(); // e.g. the browser asking for /favicon.ico
        return;
      }
      const fail = (msg: string) => {
        res.writeHead(400, { "content-type": "text/plain" }).end(msg);
        reject(new Error(msg));
      };
      if (params.get("error")) return fail(`Google returned an error: ${params.get("error")}`);
      if (params.get("state") !== state) return fail("State mismatch; ignoring this callback.");
      res
        .writeHead(200, { "content-type": "text/plain" })
        .end("Inbox connected. You can close this tab.");
      resolve(params.get("code")!);
    });
  });
  server.close();

  const { tokens } = await client.getToken({ code, codeVerifier });
  client.setCredentials(tokens);
  if (!tokens.refresh_token) {
    console.warn("Warning: no refresh token returned; you'll need to re-run auth when this expires.");
  }

  // Ask Gmail which inbox this is, rather than trusting user input.
  const gmail = google.gmail({ version: "v1", auth: client });
  const { data: profile } = await gmail.users.getProfile({ userId: "me" });
  const email = profile.emailAddress!;

  saveToken(email, tokens);
  db.prepare("INSERT OR IGNORE INTO accounts (email) VALUES (?)").run(email);
  console.log(`Connected ${email} (${profile.messagesTotal} messages in mailbox).`);
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
