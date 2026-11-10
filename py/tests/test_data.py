"""Tests for loading the eval set from the database."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from data import load_eval_set
from tests.conftest import add_label, add_message, queue


def test_loads_labeled_queued_emails_oldest_first(database: sqlite3.Connection, tmp_path: Path) -> None:
    add_message(database, "new", subject="Offer", internal_date=2_000_000_000_000)
    add_message(database, "old", subject=None, from_addr=None, body=None, internal_date=1_000_000_000_000)
    add_message(database, "unlabeled")
    add_message(database, "not-queued")
    for position, gmail_id in enumerate(["new", "old", "unlabeled"]):
        queue(database, gmail_id, position, bucket="random" if gmail_id == "old" else "keyword")
    add_label(database, "new", "offer", "Stripe | SWE")
    add_label(database, "old", "not_recruiting")
    add_label(database, "not-queued", "rejection", "Acme | X")
    database.commit()

    items = load_eval_set(tmp_path / "agent.db")

    assert [item.email.gmail_id for item in items] == ["old", "new"]
    old, new = items
    assert (old.bucket, old.category, old.application_key, old.is_recruiting) == (
        "random",
        "not_recruiting",
        None,
        False,
    )
    assert (old.email.subject, old.email.from_addr, old.email.body) == ("", "", "")  # NULLs become empty strings
    assert old.email.sent_at == datetime.fromtimestamp(1_000_000_000, tz=UTC)
    assert (new.category, new.application_key, new.is_recruiting) == ("offer", "Stripe | SWE", True)


def test_attaches_earlier_messages_of_the_same_thread(database: sqlite3.Connection, tmp_path: Path) -> None:
    database.execute("INSERT INTO accounts (email) VALUES ('other@gmail.com')")
    add_message(database, "invite", subject="Interview?", internal_date=1_000, thread_id="t1")
    add_message(database, "accept", subject="Re: Interview?", internal_date=2_000, thread_id="t1")
    add_message(database, "confirm", subject="Re: Interview?", internal_date=3_000, thread_id="t1")
    add_message(database, "later", internal_date=4_000, thread_id="t1")  # after the target: not context
    add_message(database, "elsewhere", internal_date=500, thread_id="t2")  # another thread
    add_message(database, "other-inbox", internal_date=500, thread_id="t1", account="other@gmail.com")
    queue(database, "confirm", 0)
    queue(database, "invite", 1)
    add_label(database, "confirm", "interview_scheduling", "Acme | SWE")
    add_label(database, "invite", "interview_invite", "Acme | SWE")
    database.commit()

    items = {item.email.gmail_id: item for item in load_eval_set(tmp_path / "agent.db")}

    assert [message.gmail_id for message in items["confirm"].email.thread] == ["invite", "accept"]
    assert items["confirm"].email.thread[0].subject == "Interview?"
    assert items["invite"].email.thread == ()  # first message of its thread
