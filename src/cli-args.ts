// Parsing and validating `npm run ingest` arguments.
import { assertSafeEmail } from "./google.js";

export interface IngestArgs {
  months: number;
  ratePerSecond: number;
  /** Re-download messages already stored and update them (e.g. after a parser improvement). */
  refresh: boolean;
  account?: string;
}

export const INGEST_USAGE = "Usage: npm run ingest -- [--months N] [--rate N] [--account EMAIL] [--refresh]";

const VALUE_FLAGS = ["months", "rate", "account"];

const DEFAULTS = { months: 3, ratePerSecond: 10 };

/** Throws with a usage message on unknown flags, missing values or out-of-range numbers. */
export function parseIngestArgs(argv: string[]): IngestArgs {
  const values = new Map<string, string>();
  let refresh = false;
  for (let flagIndex = 0; flagIndex < argv.length; flagIndex++) {
    const flag = argv[flagIndex] ?? "";
    if (flag === "--refresh") {
      refresh = true;
      continue;
    }
    const name = flag.replace(/^--/, "");
    const value = argv[flagIndex + 1];
    if (!flag.startsWith("--") || !VALUE_FLAGS.includes(name) || value === undefined) {
      throw new Error(`Unexpected argument "${flag}". ${INGEST_USAGE}`);
    }
    values.set(name, value);
    flagIndex++; // skip the value
  }

  const months = parseNumberInRange(values.get("months"), DEFAULTS.months, 1, 120, true, "--months");
  const ratePerSecond = parseNumberInRange(values.get("rate"), DEFAULTS.ratePerSecond, 0.5, 50, false, "--rate");
  const account = values.get("account");
  return account === undefined
    ? { months, ratePerSecond, refresh }
    : { months, ratePerSecond, refresh, account: assertSafeEmail(account) };
}

function parseNumberInRange(
  text: string | undefined,
  fallback: number,
  min: number,
  max: number,
  integer: boolean,
  flag: string,
): number {
  if (text === undefined) return fallback;
  const parsed = Number(text);
  if (!Number.isFinite(parsed) || parsed < min || parsed > max || (integer && !Number.isInteger(parsed))) {
    throw new Error(`${flag} must be ${integer ? "a whole number" : "a number"} from ${min} to ${max}, got "${text}".`);
  }
  return parsed;
}
