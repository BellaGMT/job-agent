// Type guards for narrowing untrusted values (parsed JSON, API errors) from `unknown`.

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** `obj[key]` if it is a non-empty string, else undefined. */
export function stringField(record: unknown, key: string): string | undefined {
  if (!isRecord(record)) return undefined;
  const fieldValue = record[key];
  return typeof fieldValue === "string" && fieldValue.length > 0 ? fieldValue : undefined;
}

export function errorMessage(err: unknown): string {
  if (err instanceof Error) return err.message;
  return stringField(err, "message") ?? String(err);
}
