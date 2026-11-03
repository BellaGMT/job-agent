// Parsing and validating `npm run ingest` arguments.
import { assertSafeEmail } from "./google.js";

export interface IngestArgs {
  months: number;
  ratePerSecond: number;
  account?: string;
}

export const INGEST_USAGE = "Usage: npm run ingest -- [--months N] [--rate N] [--account EMAIL]";

const DEFAULTS = { months: 3, ratePerSecond: 10 };

/** Throws with a usage message on unknown flags, missing values or out-of-range numbers. */
export function parseIngestArgs(argv: string[]): IngestArgs {
  const values = new Map<string, string>();
  for (let i = 0; i < argv.length; i += 2) {
    const flag = argv[i] ?? "";
    const value = argv[i + 1];
    const name = flag.replace(/^--/, "");
    if (!flag.startsWith("--") || !["months", "rate", "account"].includes(name) || value === undefined) {
      throw new Error(`Unexpected argument "${flag}". ${INGEST_USAGE}`);
    }
    values.set(name, value);
  }

  const months = numberIn(values.get("months"), DEFAULTS.months, 1, 120, true, "--months");
  const ratePerSecond = numberIn(values.get("rate"), DEFAULTS.ratePerSecond, 0.5, 50, false, "--rate");
  const account = values.get("account");
  return account === undefined ? { months, ratePerSecond } : { months, ratePerSecond, account: assertSafeEmail(account) };
}

function numberIn(raw: string | undefined, fallback: number, min: number, max: number, integer: boolean, flag: string): number {
  if (raw === undefined) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < min || n > max || (integer && !Number.isInteger(n))) {
    throw new Error(`${flag} must be ${integer ? "a whole number" : "a number"} from ${min} to ${max}, got "${raw}".`);
  }
  return n;
}
