// Pull recent mail from every connected inbox into SQLite.
//   npm run ingest                 all inboxes, last 3 months
//   npm run ingest -- --months 6   wider window
//   npm run ingest -- --account you@gmail.com
//   npm run ingest -- --rate 5     slower, if your project's quota is low
//   npm run ingest -- --refresh    re-download stored messages too (after a parser fix)
//
// Resumable: messages already stored are skipped, so Ctrl-C and re-run is safe.
import { google } from "googleapis";
import { type IngestArgs, parseIngestArgs } from "./cli-args.js";
import { openDb } from "./db.js";
import { statusOf } from "./gmail/retry.js";
import { gmailSource, syncAccount } from "./gmail/sync.js";
import { authorizedClient, isRevokedToken, listAccounts } from "./google.js";
import { errorMessage } from "./guards.js";
import { MessageStore } from "./store.js";

// Requests in flight at once. Pacing (--rate) limits how fast they start; this
// bounds bursts when responses are slow.
const CONCURRENCY = 4;

async function main(args: IngestArgs): Promise<void> {
  const { accounts, ignored } = args.account ? { accounts: [args.account], ignored: [] } : listAccounts();
  for (const name of ignored) console.warn(`Ignoring tokens/${name}.json: not named like an inbox address.`);
  if (accounts.length === 0) throw new Error("No inboxes connected. Run: npm run auth");

  const db = openDb();
  try {
    const store = new MessageStore(db);
    for (const email of accounts) {
      try {
        const source = gmailSource(google.gmail({ version: "v1", auth: authorizedClient(email) }));
        const syncResult = await syncAccount(source, store, email, {
          months: args.months,
          ratePerSecond: args.ratePerSecond,
          refresh: args.refresh,
          concurrency: CONCURRENCY,
          log: (line) => console.log(line),
        });
        if (syncResult.vanished)
          console.log(`  ${syncResult.vanished} message(s) were deleted before they could be fetched`);
        if (!syncResult.completed) {
          console.warn(
            `  ${syncResult.malformed.length} malformed message(s) skipped; sync not marked complete, they'll be retried next run`,
          );
          process.exitCode = 1;
        }
      } catch (err) {
        // One failing inbox shouldn't block the others; the exit code still reports it.
        process.exitCode = 1;
        if (isRevokedToken(err)) {
          console.error(`${email}: access expired or revoked. Run: npm run auth (pick ${email})`);
        } else {
          const status = statusOf(err);
          console.error(
            `${email}: sync failed${status ? ` (HTTP ${status})` : ""}: ${errorMessage(err)}. Already-fetched messages are saved; re-run to continue.`,
          );
        }
      }
    }
    console.table(store.countsByAccount());
  } finally {
    db.close();
  }
}

let args: IngestArgs;
try {
  args = parseIngestArgs(process.argv.slice(2));
} catch (err) {
  console.error(errorMessage(err)); // a usage mistake, not a sync failure
  process.exit(2);
}

main(args).catch((err: unknown) => {
  // The full Gaxios error is a 100-line dump; status + message is what's actionable.
  const status = statusOf(err);
  console.error(`Sync failed${status ? ` (HTTP ${status})` : ""}: ${errorMessage(err)}`);
  console.error("Already-fetched messages are saved; re-run to continue.");
  process.exit(1);
});
