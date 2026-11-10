"""Load emails and their hand labels from the shared SQLite database."""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from db import DB_PATH, connect
from label import NO_KEY

MESSAGE_ID = re.compile(r"<[^<>\s]+>")


@dataclass(frozen=True)
class Email:
    account: str  # the inbox it came from (the mailbox owner)
    gmail_id: str
    from_addr: str
    to_addr: str
    subject: str
    sent_at: datetime
    body: str
    thread_id: str = ""  # Gmail's thread id; only meaningful within one inbox
    rfc_message_id: str | None = None  # the Message-ID header, the same in every inbox
    # Message-IDs this email replies to (In-Reply-To and References): the
    # conversation chain, which also connects copies of a thread across inboxes.
    references: tuple[str, ...] = ()
    # Earlier messages of the same Gmail thread in the same inbox, oldest first.
    # Context for the classifier: a short reply ("Sounds good") or a calendar
    # notice often means nothing without the messages before it.
    thread: tuple[Email, ...] = ()


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
        sent_at=datetime.fromtimestamp(row["internal_date"] / 1000, tz=UTC),
        body=row["body_text"] or "",
        thread_id=row["thread_id"],
        rfc_message_id=row["rfc_message_id"],
        references=message_ids(f"{row['in_reply_to'] or ''} {row['references_ids'] or ''}"),
    )


def message_ids(header: str) -> tuple[str, ...]:
    """'<a@x> <b@y>' -> ('<a@x>', '<b@y>'): the ids in a References or In-Reply-To header, deduplicated."""
    return tuple(dict.fromkeys(MESSAGE_ID.findall(header)))


def load_eval_set(db_path: Path | str = DB_PATH, from_position: int = 0) -> list[LabeledEmail]:
    """Every labeled email in the label queue at `from_position` or later, oldest first, with its thread.

    A `from_position` above 0 selects a batch queued later, e.g. a held-out set.
    """
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            """SELECT m.*, q.bucket, l.category, l.application_key
               FROM labels l
               JOIN messages m USING (account, gmail_id)
               JOIN label_queue q USING (account, gmail_id)
               WHERE q.position >= ?
               ORDER BY m.internal_date""",
            (from_position,),
        ).fetchall()
        threads = _earlier_thread_messages(connection)
    return [
        LabeledEmail(
            replace(_email(row), thread=threads.get((row["account"], row["gmail_id"]), ())),
            row["bucket"],
            row["category"],
            row["application_key"],
        )
        for row in rows
    ]


def _earlier_thread_messages(connection: sqlite3.Connection) -> dict[tuple[str, str], tuple[Email, ...]]:
    """For each queued email, the messages before it in its thread, in one query (no per-email lookups).

    Threads are scoped to one inbox: Gmail thread ids are per mailbox, and the
    mailbox owner is part of what the classifier is told.
    """
    rows = connection.execute(
        """SELECT q.account AS target_account, q.gmail_id AS target_id, earlier.*
           FROM label_queue q
           JOIN messages target USING (account, gmail_id)
           JOIN messages earlier
             ON earlier.account = target.account
            AND earlier.thread_id = target.thread_id
            AND (earlier.internal_date, earlier.gmail_id) < (target.internal_date, target.gmail_id)
           ORDER BY earlier.internal_date, earlier.gmail_id"""
    ).fetchall()
    threads: dict[tuple[str, str], list[Email]] = defaultdict(list)
    for row in rows:
        threads[(row["target_account"], row["target_id"])].append(_email(row))
    return {target: tuple(messages) for target, messages in threads.items()}
