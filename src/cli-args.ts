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
  for (let flagIndex = 0; flagIndex < argv.length; flagIndex += 2) {
    const flag = argv[flagIndex] ?? "";
    const value = argv[flagIndex + 1];
    const name = flag.replace(/^--/, "");
    if (!flag.startsWith("--") || !["months", "rate", "account"].includes(name) || value === undefined) {
      throw new Error(`Unexpected argument "${flag}". ${INGEST_USAGE}`);
    }
    values.set(name, value);
  }

  const months = parseNumberInRange(values.get("months"), DEFAULTS.months, 1, 120, true, "--months");
  const ratePerSecond = parseNumberInRange(values.get("rate"), DEFAULTS.ratePerSecond, 0.5, 50, false, "--rate");
  const account = values.get("account");
  return account === undefined
    ? { months, ratePerSecond }
    : { months, ratePerSecond, account: assertSafeEmail(account) };
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
