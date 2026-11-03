// Connect one Gmail inbox. Run once per inbox: npm run auth
//
// Flow (OAuth 2.0 for installed apps, "loopback" variant):
//   1. Start a tiny HTTP server on 127.0.0.1 at a random free port.
//   2. Send you to Google's consent page with that address as the redirect.
//   3. Google redirects back to the server with a one-time code.
//   4. Exchange code -> tokens (access + refresh), save them under tokens/.
// PKCE and `state` stop another local process from hijacking or forging step 3.
import crypto from "node:crypto";
import { google, Auth } from "googleapis";
import { openDb } from "./db.js";
import { assertSafeEmail, newOAuthClient, parseCredentials, saveToken, SCOPES } from "./google.js";
import { errorMessage } from "./guards.js";
import { startCallbackServer } from "./oauth-callback.js";

const SIGN_IN_TIMEOUT_MS = 5 * 60_000;

async function main(): Promise<void> {
  const callback = await startCallbackServer();
  try {
    const client = newOAuthClient(callback.redirectUri);
    const state = crypto.randomBytes(16).toString("hex");
    const { codeVerifier, codeChallenge } = await client.generateCodeVerifierAsync();
    if (!codeChallenge) throw new Error("Could not generate a PKCE challenge.");

    const url = client.generateAuthUrl({
      access_type: "offline", // ask for a refresh token
      prompt: "consent", // force one even if you've consented before
      scope: SCOPES,
      state,
      code_challenge: codeChallenge,
      code_challenge_method: Auth.CodeChallengeMethod.S256,
    });
    console.log("\nOpen this URL and pick the inbox to connect:\n\n" + url + "\n");

    const code = await callback.waitForCode(state, SIGN_IN_TIMEOUT_MS);
    const { tokens } = await client.getToken({ code, codeVerifier });
    client.setCredentials(parseCredentials(tokens));
    if (!tokens.refresh_token) {
      console.warn("Warning: no refresh token returned; you'll need to re-run auth when this expires.");
    }

    // Ask Gmail which inbox this is, rather than trusting user input.
    const { data: profile } = await google.gmail({ version: "v1", auth: client }).users.getProfile({ userId: "me" });
    if (!profile.emailAddress) throw new Error("Gmail did not return the inbox address.");
    const email = assertSafeEmail(profile.emailAddress);

    saveToken(email, tokens);
    openDb().prepare("INSERT OR IGNORE INTO accounts (email) VALUES (?)").run(email);
    const total = typeof profile.messagesTotal === "number" ? ` (${profile.messagesTotal} messages in mailbox)` : "";
    console.log(`Connected ${email}${total}.`);
  } finally {
    callback.close();
  }
}

main().catch((err: unknown) => {
  console.error(errorMessage(err));
  process.exit(1);
});
