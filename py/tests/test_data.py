"""Tests for loading the eval set from the database."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
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
    assert (old.bucket, old.category, old.application_key, old.is_recruiting) == ("random", "not_recruiting", None, False)
    assert (old.email.subject, old.email.from_addr, old.email.body) == ("", "", "")  # NULLs become empty strings
    assert old.email.sent_at == datetime.fromtimestamp(1_000_000_000, tz=timezone.utc)
    assert (new.category, new.application_key, new.is_recruiting) == ("offer", "Stripe | SWE", True)
