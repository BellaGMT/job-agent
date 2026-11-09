"""Tests for the labeling tool's logic: sampling, keys, renames, duplicate detection."""

from __future__ import annotations

import sqlite3
from collections import Counter

import pytest

from label import (
    MAX_PER_SENDER_DOMAIN,
    add_targeted,
    base_domain,
    choose_sample,
    cmd_show,
    describe_label,
    existing_keys,
    find_emails,
    near_duplicate_keys,
    normalize_key,
    rename_key,
    sender_domain,
    show,
)
from tests.conftest import add_label, add_message, queue


def sample_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT account, gmail_id, from_addr, subject, body_text AS body FROM messages ORDER BY gmail_id"
    ).fetchall()


# --- domains ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_address", "expected"),
    [
        ("Handshake <handshake@notifications.joinhandshake.com>", "joinhandshake.com"),
        ("noreply@mail.amazon.jobs", "amazon.jobs"),
        ("Bob <BOB@Example.COM>", "example.com"),
        ("no address here", "?"),
        (None, "?"),
    ],
)
def test_sender_domain_groups_subdomains(from_address: str | None, expected: str) -> None:
    assert sender_domain(from_address) == expected


def test_base_domain_edge_cases() -> None:
    assert base_domain("localhost") == "?"
    assert base_domain(None) == "?"


# --- sampling -----------------------------------------------------------------


def test_sample_is_deterministic_for_a_seed(database: sqlite3.Connection) -> None:
    for number in range(30):
        add_message(database, f"g{number:02}", subject="Interview" if number % 2 else "Sale")
    rows = sample_rows(database)
    first = [(row["gmail_id"], bucket) for row, bucket in choose_sample(rows, 10, seed=7, keyword_share=0.7)]
    second = [(row["gmail_id"], bucket) for row, bucket in choose_sample(rows, 10, seed=7, keyword_share=0.7)]
    other_seed = [(row["gmail_id"], bucket) for row, bucket in choose_sample(rows, 10, seed=8, keyword_share=0.7)]
    assert first == second
    assert first != other_seed


def test_sample_splits_keyword_and_random_by_share(database: sqlite3.Connection) -> None:
    for number in range(20):
        add_message(database, f"k{number:02}", from_addr=f"hr@company{number}.com", subject="Your application")
        add_message(database, f"r{number:02}", from_addr=f"deals@shop{number}.com", subject="Sale")
    picked = choose_sample(sample_rows(database), 10, seed=1, keyword_share=0.7)
    assert Counter(bucket for _, bucket in picked) == {"keyword": 7, "random": 3}
    assert all(row["gmail_id"].startswith("k") for row, bucket in picked if bucket == "keyword")
    assert all(row["gmail_id"].startswith("r") for row, bucket in picked if bucket == "random")
    assert len({row["gmail_id"] for row, _ in picked}) == 10  # no email picked twice


def test_sample_caps_each_sender_domain_and_fills_the_gap_with_random(database: sqlite3.Connection) -> None:
    for number in range(20):
        add_message(database, f"k{number:02}", from_addr="jobs@alerts.linkedin.com", subject="New jobs: apply now")
        add_message(database, f"r{number:02}", from_addr=f"deals@shop{number}.com", subject="Sale")
    picked = choose_sample(sample_rows(database), 10, seed=1, keyword_share=0.7)
    keyword_picks = [row for row, bucket in picked if bucket == "keyword"]
    assert len(keyword_picks) == MAX_PER_SENDER_DOMAIN
    assert len(picked) == 10


def test_sample_smaller_than_requested_when_messages_run_out(database: sqlite3.Connection) -> None:
    add_message(database, "only", subject="Interview")
    assert len(choose_sample(sample_rows(database), 10, seed=1, keyword_share=0.7)) == 1


# --- application keys ---------------------------------------------------------


def test_normalize_key_collapses_spacing_and_reuses_known_company_spelling() -> None:
    assert normalize_key("google|  Backend   Engineer ", ["Google | SWE"]) == "Google | Backend Engineer"
    assert normalize_key("Stripe |", []) == "Stripe | ?"
    assert normalize_key("Acme | Role | With Pipe", []) == "Acme | Role | With Pipe"


def test_existing_keys_most_recent_first(database: sqlite3.Connection) -> None:
    for gmail_id, key in [("a", "Old | X"), ("b", "Newer | Y"), ("c", "Old | X")]:
        add_message(database, gmail_id)
        add_label(database, gmail_id, "rejection", key)
    add_message(database, "d")
    add_label(database, "d", "job_alert")
    assert existing_keys(database) == ["Old | X", "Newer | Y"]
    assert existing_keys(database, excluding="Old | X") == ["Newer | Y"]


def test_rename_key_updates_every_label_and_reports_merges(database: sqlite3.Connection) -> None:
    for gmail_id, key in [("a", "Github | SWE  I"), ("b", "Github | SWE  I"), ("c", "GitHub | SWE I")]:
        add_message(database, gmail_id)
        add_label(database, gmail_id, "rejection", key)
    assert rename_key(database, "Github | SWE  I", "github |  SWE I") == (2, "GitHub | SWE I", True)
    keys = {row[0] for row in database.execute("SELECT application_key FROM labels")}
    assert keys == {"GitHub | SWE I"}


def test_rename_key_rejects_unknown_old_key_and_new_key_without_pipe(database: sqlite3.Connection) -> None:
    add_message(database, "a")
    add_label(database, "a", "rejection", "Acme | X")
    with pytest.raises(ValueError, match="No labels use the key"):
        rename_key(database, "Nope | X", "A | B")
    with pytest.raises(ValueError, match="needs a '\\|'"):
        rename_key(database, "Acme | X", "Acme")
    assert database.execute("SELECT application_key FROM labels").fetchone()[0] == "Acme | X"


def test_near_duplicate_keys_finds_typos_only() -> None:
    keys = ["Netflix | SWE 3 - Ink", "Netflix | swe 3 ink", "Google | SWE", "Google | SRE"]
    assert near_duplicate_keys(keys) == [["Netflix | SWE 3 - Ink", "Netflix | swe 3 ink"]]
    assert near_duplicate_keys([]) == []


# --- queue --------------------------------------------------------------------


def test_add_targeted_appends_and_ignores_already_queued(database: sqlite3.Connection) -> None:
    for gmail_id in ["a", "b", "c"]:
        add_message(database, gmail_id)
    queue(database, "a", position=0)
    emails = database.execute("SELECT account, gmail_id FROM messages ORDER BY gmail_id").fetchall()
    assert add_targeted(database, emails) == 2
    rows = database.execute("SELECT gmail_id, position, bucket FROM label_queue ORDER BY position").fetchall()
    assert [(row["gmail_id"], row["bucket"]) for row in rows] == [
        ("a", "keyword"),
        ("b", "targeted"),
        ("c", "targeted"),
    ]


def test_queue_rejects_unknown_buckets(database: sqlite3.Connection) -> None:
    add_message(database, "a")
    with pytest.raises(sqlite3.IntegrityError):
        queue(database, "a", position=0, bucket="other")


# --- show ---------------------------------------------------------------------


def test_find_emails_returns_each_inbox_copy_with_its_label(database: sqlite3.Connection) -> None:
    database.execute("INSERT INTO accounts (email) VALUES ('work@example.com')")
    add_message(database, "shared", subject="Interview")
    database.execute(
        "INSERT INTO messages (account, gmail_id, thread_id, internal_date, subject) "
        "VALUES ('work@example.com', 'shared', 't', 1, 'Interview (forwarded)')"
    )
    add_label(database, "shared", "interview_invite", "Stripe | SWE")

    copies = find_emails(database, "shared")

    assert [copy["account"] for copy in copies] == ["me@gmail.com", "work@example.com"]
    assert describe_label(copies[0]) == "Labeled interview_invite | Stripe | SWE"
    assert describe_label(copies[1]) == "Not labeled"
    assert find_emails(database, "missing") == []


def test_show_prints_the_whole_body_when_unlimited(
    database: sqlite3.Connection, capsys: pytest.CaptureFixture[str]
) -> None:
    long_body = "word " * 1000 + "THE END"
    add_message(database, "long", body=long_body)
    email = find_emails(database, "long")[0]

    show(email, "header", body_limit=None)
    full = capsys.readouterr().out
    show(email, "header")
    preview = capsys.readouterr().out

    assert "THE END" in full and "(m for more)" not in full
    assert "THE END" not in preview and "(m for more)" in preview


def test_show_command_rejects_unknown_ids(database: sqlite3.Connection) -> None:
    with pytest.raises(SystemExit, match="No email with id 'nope'"):
        cmd_show(database, "nope")
