"""Opening the SQLite database shared with the TypeScript sync."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
# AGENT_DB lets tests and experiments point at a throwaway database.
DB_PATH = Path(os.environ.get("AGENT_DB", REPO_ROOT / "data" / "agent.db"))
# The same file src/db.ts applies, so both languages always agree on the schema.
SCHEMA_PATH = REPO_ROOT / "schema.sql"


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open the database with name-addressable rows and apply the (idempotent) schema."""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return connection
