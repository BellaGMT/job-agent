// Opening and migrating the SQLite database shared by the TypeScript and Python sides.

import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

// AGENT_DB lets tests point at a throwaway database instead of your real one.
export const DB_PATH = process.env["AGENT_DB"] ?? "data/agent.db";

// One schema file shared with the Python tools (py/db.py), so the two sides can't drift.
const SCHEMA_FILE = new URL("../schema.sql", import.meta.url);

/** Create missing tables and indexes. Safe to run on every start. */
export function migrate(db: Database.Database): void {
  db.exec(fs.readFileSync(SCHEMA_FILE, "utf8"));
}

/** Open (creating if needed) and migrate the database. Pass ":memory:" in tests. */
export function openDb(file: string = DB_PATH): Database.Database {
  if (file !== ":memory:") fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new Database(file);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON"); // SQLite ignores REFERENCES clauses unless this is on
  migrate(db);
  return db;
}
