"""Load emails and their hand labels from the shared SQLite database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from label import DB_PATH, NO_KEY


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


def _email(r: sqlite3.Row) -> Email:
    return Email(
        account=r["account"],
        gmail_id=r["gmail_id"],
        from_addr=r["from_addr"] or "",
        to_addr=r["to_addr"] or "",
        subject=r["subject"] or "",
        sent_at=datetime.fromtimestamp(r["internal_date"] / 1000, tz=timezone.utc),
        body=r["body_text"] or "",
    )


def load_eval_set() -> list[LabeledEmail]:
    """Every labeled email, oldest first."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT m.*, q.bucket, l.category, l.application_key
           FROM labels l
           JOIN messages m USING (account, gmail_id)
           JOIN label_queue q USING (account, gmail_id)
           ORDER BY m.internal_date"""
    ).fetchall()
    conn.close()
    return [LabeledEmail(_email(r), r["bucket"], r["category"], r["application_key"]) for r in rows]
