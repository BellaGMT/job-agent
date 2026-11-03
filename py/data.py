"""Load emails and their hand labels from the shared SQLite database."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from db import DB_PATH, connect
from label import NO_KEY


@dataclass(frozen=True)
class Email:
    account: str  # the inbox it came from (the mailbox owner)
    gmail_id: str
    from_addr: str
    to_addr: str
    subject: str
    sent_at: datetime
    body: str


@dataclass(frozen=True)
class LabeledEmail:
    email: Email
    bucket: str  # 'keyword' | 'random' | 'targeted'
    category: str  # expected category (ground truth)
    application_key: str | None  # "Company | Role", None for non-recruiting

    @property
    def is_recruiting(self) -> bool:
        return self.category not in NO_KEY


def _email(row: sqlite3.Row) -> Email:
    return Email(
        account=row["account"],
        gmail_id=row["gmail_id"],
        from_addr=row["from_addr"] or "",
        to_addr=row["to_addr"] or "",
        subject=row["subject"] or "",
        sent_at=datetime.fromtimestamp(row["internal_date"] / 1000, tz=timezone.utc),
        body=row["body_text"] or "",
    )


def load_eval_set(db_path: Path | str = DB_PATH) -> list[LabeledEmail]:
    """Every labeled email that is in the label queue, oldest first."""
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            """SELECT m.*, q.bucket, l.category, l.application_key
               FROM labels l
               JOIN messages m USING (account, gmail_id)
               JOIN label_queue q USING (account, gmail_id)
               ORDER BY m.internal_date"""
        ).fetchall()
    return [LabeledEmail(_email(row), row["bucket"], row["category"], row["application_key"]) for row in rows]
