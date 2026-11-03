// Pull recent mail from every connected inbox into SQLite.
//   npm run ingest                 all inboxes, last 3 months
//   npm run ingest -- --months 6   wider window
//   npm run ingest -- --account you@gmail.com
//   npm run ingest -- --rate 5     slower, if your project's quota is low
//
// Resumable: messages already stored are skipped, so Ctrl-C and re-run is safe.
import { google } from "googleapis";
import { parseIngestArgs } from "./cli-args.js";
import { openDb } from "./db.js";
import { gmailSource, syncAccount } from "./gmail/sync.js";
import { statusOf } from "./gmail/retry.js";
import { authorizedClient, isRevokedToken, listAccounts } from "./google.js";
import { errorMessage } from "./guards.js";
import { MessageStore } from "./store.js";

// Requests in flight at once. Pacing (--rate) limits how fast they start; this
// bounds bursts when responses are slow.
const CONCURRENCY = 4;

async function main(): Promise<void> {
  const args = parseIngestArgs(process.argv.slice(2));
  const accounts = args.account ? [args.account] : listAccounts();
  if (accounts.length === 0) throw new Error("No inboxes connected. Run: npm run auth");

  const db = openDb();
  try {
    const store = new MessageStore(db);
    for (const email of accounts) {
      const source = gmailSource(google.gmail({ version: "v1", auth: authorizedClient(email) }));
      try {
        const r = await syncAccount(source, store, email, {
          months: args.months,
          ratePerSecond: args.ratePerSecond,
          concurrency: CONCURRENCY,
          log: (line) => console.log(line),
        });
        if (r.vanished) console.log(`  ${r.vanished} message(s) were deleted before they could be fetched`);
      } catch (err) {
        if (!isRevokedToken(err)) throw err;
        // One revoked inbox shouldn't block the others.
        console.error(`${email}: access expired or revoked. Run: npm run auth (pick ${email})`);
        process.exitCode = 1;
      }
    }
    console.table(store.countsByAccount());
  } finally {
    db.close();
  }
}

main().catch((err: unknown) => {
  // The full Gaxios error is a 100-line dump; status + message is what's actionable.
  const status = statusOf(err);
  console.error(`Sync failed${status ? ` (HTTP ${status})` : ""}: ${errorMessage(err)}`);
  console.error("Already-fetched messages are saved; re-run to continue.");
  process.exit(1);
});
