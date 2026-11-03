"""Tests for the sender-domain report."""

from __future__ import annotations

import sqlite3

from explore import domain_stats
from tests.conftest import add_message


def test_counts_per_base_domain_and_records_keyword_misses(database: sqlite3.Connection) -> None:
    add_message(database, "a", from_addr="Tesla <recruiting@tesla.com>", subject="Your application")
    add_message(database, "b", from_addr="Chris <chris@tesla.com>", subject="Tesla Sync", body="See you then")
    add_message(database, "c", from_addr="Shop <x@mail.shop.com>", subject="Apply this coupon")
    add_message(database, "d", from_addr="Shop <x@news.shop.com>", subject=None, body="hi")
    messages = database.execute("SELECT from_addr, subject, body_text AS body FROM messages").fetchall()

    stats = domain_stats(messages)

    assert stats.emails == {"tesla.com": 2, "shop.com": 2}  # subdomains grouped
    assert stats.keyword_matches == {"tesla.com": 1, "shop.com": 1}  # "Apply" matches the keyword filter
    assert stats.misses["tesla.com"] == [("Chris <chris@tesla.com>", "Tesla Sync")]
    assert stats.misses["shop.com"] == [("Shop <x@news.shop.com>", "(no subject)")]
