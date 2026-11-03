// Google OAuth plumbing: client secrets, per-inbox tokens, authorized clients.
import fs from "node:fs";
import path from "node:path";
import { google, Auth } from "googleapis";
import { isRecord, stringField } from "./guards.js";

// Least privilege: read-only until a feature needs more. Calendar and send
// scopes are requested when those features ship, with explicit re-consent.
export const SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"];

const CREDENTIALS_PATH = "credentials.json";
export const TOKEN_DIR = "tokens";

// Account emails become token file names. The requirement is that a name can't
// escape the token directory or be unusable as a file name on Windows; beyond
// that, any address Google returns is accepted (Workspace allows e.g. o'brien@).
const UNSAFE_FILENAME_CHARS = /[\/\\:*?"<>|\u0000-\u001f]/;

export function isSafeAccountName(email: string): boolean {
  const at = email.indexOf("@");
  return (
    at > 0 &&
    at === email.lastIndexOf("@") &&
    at < email.length - 1 &&
    !email.startsWith(".") &&
    !email.includes("..") &&
    !/\s/.test(email) &&
    !UNSAFE_FILENAME_CHARS.test(email)
  );
}

export interface ClientSecrets {
  clientId: string;
  clientSecret: string;
}

export function assertSafeEmail(email: string): string {
  if (!isSafeAccountName(email)) throw new Error(`Not a valid inbox address: ${JSON.stringify(email)}`);
  return email;
}

/** Validate the OAuth client JSON downloaded from Google Cloud ("installed" or "web" section). */
export function parseClientSecrets(clientFile: unknown): ClientSecrets {
  const section = isRecord(clientFile) ? (clientFile["installed"] ?? clientFile["web"]) : undefined;
  const clientId = stringField(section, "client_id");
  const clientSecret = stringField(section, "client_secret");
  if (!clientId || !clientSecret) {
    throw new Error(`${CREDENTIALS_PATH} doesn't look like an OAuth client file.`);
  }
  return { clientId, clientSecret };
}

/** Validate a stored or freshly issued token set. Unknown fields are dropped. */
export function parseCredentials(tokenData: unknown): Auth.Credentials {
  if (!isRecord(tokenData)) throw new Error("Token data is not an object.");
  const credentials: Auth.Credentials = {};
  const accessToken = stringField(tokenData, "access_token");
  const refreshToken = stringField(tokenData, "refresh_token");
  const tokenType = stringField(tokenData, "token_type");
  const scope = stringField(tokenData, "scope");
  if (accessToken) credentials.access_token = accessToken;
  if (refreshToken) credentials.refresh_token = refreshToken;
  if (tokenType) credentials.token_type = tokenType;
  if (scope) credentials.scope = scope;
  const expiryDate = tokenData["expiry_date"];
  if (typeof expiryDate === "number") credentials.expiry_date = expiryDate;
  if (!credentials.access_token && !credentials.refresh_token) throw new Error("Token data has no access or refresh token.");
  return credentials;
}

function readJson(file: string): unknown {
  return JSON.parse(fs.readFileSync(file, "utf8")) as unknown;
}

function readClientSecrets(): ClientSecrets {
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    throw new Error(
      `Missing ${CREDENTIALS_PATH}. Download your OAuth "Desktop app" client JSON ` +
        `from Google Cloud and save it in the project root (see README).`,
    );
  }
  return parseClientSecrets(readJson(CREDENTIALS_PATH));
}

export function newOAuthClient(redirectUri?: string): Auth.OAuth2Client {
  const { clientId, clientSecret } = readClientSecrets();
  return new google.auth.OAuth2(clientId, clientSecret, redirectUri);
}

const tokenPath = (email: string, dir: string): string => path.join(dir, `${assertSafeEmail(email)}.json`);

/**
 * Merge new tokens into the stored ones and write atomically.
 *
 * Google returns a refresh_token only on first consent; later refreshes carry
 * just a new access_token, so merging keeps the refresh_token. Writing to a
 * temp file and renaming means a crash mid-write can't leave a corrupt token.
 */
export function saveToken(email: string, tokens: Auth.Credentials, dir = TOKEN_DIR): void {
  fs.mkdirSync(dir, { recursive: true });
  const file = tokenPath(email, dir);
  const existing = fs.existsSync(file) ? parseCredentials(readJson(file)) : {};
  const merged = parseCredentials({ ...existing, ...dropNullish(tokens) });
  const tempFile = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tempFile, JSON.stringify(merged, null, 2), { mode: 0o600 }); // owner-only on POSIX
  fs.renameSync(tempFile, file);
}

export function loadToken(email: string, dir = TOKEN_DIR): Auth.Credentials {
  const file = tokenPath(email, dir);
  if (!fs.existsSync(file)) throw new Error(`No token for ${email}. Run: npm run auth`);
  return parseCredentials(readJson(file));
}

/** Connected inboxes, plus any .json files in the token directory that aren't named like one (reported, not hidden). */
export function listAccounts(dir = TOKEN_DIR): { accounts: string[]; ignored: string[] } {
  if (!fs.existsSync(dir)) return { accounts: [], ignored: [] };
  const names = fs
    .readdirSync(dir)
    .filter((fileName) => fileName.endsWith(".json"))
    .map((fileName) => fileName.slice(0, -".json".length))
    .sort();
  return { accounts: names.filter(isSafeAccountName), ignored: names.filter((name) => !isSafeAccountName(name)) };
}

export function authorizedClient(email: string): Auth.OAuth2Client {
  const client = newOAuthClient();
  client.setCredentials(loadToken(email));
  // The library refreshes access tokens automatically; persist each new one.
  client.on("tokens", (refreshedTokens) => saveToken(email, refreshedTokens));
  return client;
}

/**
 * Apps in "Testing" status get refresh tokens that Google revokes after 7 days.
 * That surfaces as invalid_grant, which needs a re-consent, not a retry.
 */
export function isRevokedToken(err: unknown): boolean {
  const data = isRecord(err) && isRecord(err["response"]) ? err["response"]["data"] : undefined;
  return stringField(data, "error") === "invalid_grant" || /invalid_grant/.test(stringField(err, "message") ?? "");
}

function dropNullish(tokens: Auth.Credentials): Record<string, unknown> {
  return Object.fromEntries(Object.entries(tokens).filter(([, v]) => v !== null && v !== undefined));
}
