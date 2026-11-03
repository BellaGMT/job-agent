// Type guards for narrowing untrusted values (parsed JSON, API errors) from `unknown`.

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `obj[key]` if it is a non-empty string, else undefined. */
export function stringField(obj: unknown, key: string): string | undefined {
  if (!isRecord(obj)) return undefined;
  const v = obj[key];
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return stringField(err, "message") ?? String(err);
}
