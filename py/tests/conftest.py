"""Shared fixtures: a throwaway database with the real schema."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from db import connect


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "agent.db")
    connection.execute("INSERT INTO accounts (email) VALUES ('me@gmail.com')")
    yield connection
    connection.close()


def add_message(
    connection: sqlite3.Connection,
    gmail_id: str,
    from_addr: str | None = "Someone <someone@example.com>",
    subject: str | None = "Hello",
    body: str | None = "Just saying hi.",
    internal_date: int = 1_761_600_000_000,
    thread_id: str | None = None,  # defaults to a thread of its own
    account: str = "me@gmail.com",
) -> None:
    connection.execute(
        "INSERT INTO messages (account, gmail_id, thread_id, internal_date, from_addr, subject, body_text) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (account, gmail_id, thread_id or f"t-{gmail_id}", internal_date, from_addr, subject, body),
    )


def add_label(connection: sqlite3.Connection, gmail_id: str, category: str, key: str | None = None) -> None:
    connection.execute(
        "INSERT INTO labels (account, gmail_id, category, application_key) VALUES ('me@gmail.com', ?, ?, ?)",
        (gmail_id, category, key),
    )


def queue(connection: sqlite3.Connection, gmail_id: str, position: int, bucket: str = "keyword") -> None:
    connection.execute(
        "INSERT INTO label_queue (position, account, gmail_id, bucket) VALUES (?, 'me@gmail.com', ?, ?)",
        (position, gmail_id, bucket),
    )
