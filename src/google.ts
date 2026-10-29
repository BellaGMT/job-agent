// Shared Google OAuth plumbing: client secrets, per-inbox tokens, authorized clients.
import fs from "node:fs";
import path from "node:path";
import { google, Auth } from "googleapis";

// Least privilege: read-only until a feature needs more. Calendar and send
// scopes are requested when those features ship, with explicit re-consent.
export const SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"];

const CREDENTIALS_PATH = "credentials.json";
const TOKEN_DIR = "tokens";

function readClientSecrets(): { client_id: string; client_secret: string } {
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    throw new Error(
      `Missing ${CREDENTIALS_PATH}. Download your OAuth "Desktop app" client JSON ` +
        `from Google Cloud and save it in the project root (see README).`,
    );
  }
  const raw = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, "utf8"));
  const c = raw.installed ?? raw.web;
  if (!c?.client_id || !c?.client_secret) {
    throw new Error(`${CREDENTIALS_PATH} doesn't look like an OAuth client file.`);
  }
  return c;
}

export function newOAuthClient(redirectUri?: string): Auth.OAuth2Client {
  const { client_id, client_secret } = readClientSecrets();
  return new google.auth.OAuth2(client_id, client_secret, redirectUri);
}

const tokenPath = (email: string) => path.join(TOKEN_DIR, `${email}.json`);

export function saveToken(email: string, tokens: Auth.Credentials): void {
  fs.mkdirSync(TOKEN_DIR, { recursive: true });
  // Google only returns a refresh_token on first consent; refreshes return just
  // a new access_token. Merge so a refresh never erases the refresh_token.
  const existing = fs.existsSync(tokenPath(email))
    ? JSON.parse(fs.readFileSync(tokenPath(email), "utf8"))
    : {};
  fs.writeFileSync(tokenPath(email), JSON.stringify({ ...existing, ...tokens }, null, 2), {
    mode: 0o600, // tokens are passwords to your inbox
  });
}

export function listAccounts(): string[] {
  if (!fs.existsSync(TOKEN_DIR)) return [];
  return fs
    .readdirSync(TOKEN_DIR)
    .filter((f) => f.endsWith(".json"))
    .map((f) => f.slice(0, -".json".length))
    .sort();
}

export function authorizedClient(email: string): Auth.OAuth2Client {
  if (!fs.existsSync(tokenPath(email))) {
    throw new Error(`No token for ${email}. Run: npm run auth`);
  }
  const client = newOAuthClient();
  client.setCredentials(JSON.parse(fs.readFileSync(tokenPath(email), "utf8")));
  // The library refreshes access tokens automatically; persist each new one.
  client.on("tokens", (t) => saveToken(email, t));
  return client;
}

// Apps in "Testing" status get refresh tokens that Google revokes after 7 days.
// That surfaces as invalid_grant, which needs a re-consent, not a retry.
export function isRevokedToken(err: unknown): boolean {
  const e = err as { message?: string; response?: { data?: { error?: string } } };
  return e?.response?.data?.error === "invalid_grant" || /invalid_grant/.test(e?.message ?? "");
}
