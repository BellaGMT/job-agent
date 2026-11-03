"""Top sender domains, and how many of their emails the keyword pre-filter matches.

uv run python explore.py                         # top 20 table
uv run python explore.py tesla.com google.com    # plus non-matching emails for those domains
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, field

from db import DB_PATH, connect
from label import matches_keywords, sender_domain


@dataclass
class DomainStats:
    emails: Counter[str] = field(default_factory=Counter)  # domain -> number of emails
    keyword_matches: Counter[str] = field(default_factory=Counter)  # domain -> emails matching KEYWORDS
    # domain -> (sender, subject) of emails with no keyword match
    misses: defaultdict[str, list[tuple[str, str]]] = field(default_factory=lambda: defaultdict(list))


def domain_stats(messages: Iterable[sqlite3.Row]) -> DomainStats:
    """Count emails per sender domain, using the same text the sampler matches on."""
    stats = DomainStats()
    for message in messages:
        domain = sender_domain(message["from_addr"])
        stats.emails[domain] += 1
        if matches_keywords(message):
            stats.keyword_matches[domain] += 1
        else:
            stats.misses[domain].append((message["from_addr"] or "?", message["subject"] or "(no subject)"))
    return stats


def print_report(stats: DomainStats, message_count: int, domains_to_inspect: list[str]) -> None:
    print(f"{'domain':40} {'emails':>7} {'keyword hits':>13}")
    for domain, email_count in stats.emails.most_common(20):
        print(f"{domain:40} {email_count:7} {stats.keyword_matches[domain]:13}")
    print(f"\n{message_count} emails from {len(stats.emails)} domains")

    # Domains named on the command line: list the emails the keyword filter missed.
    for requested in domains_to_inspect:
        domain = requested.lower()
        if domain not in stats.emails:
            print(f"\n{domain}: no emails from this domain")
            continue
        print(f"\n{domain}: {len(stats.misses[domain])} of {stats.emails[domain]} emails with no keyword match")
        for sender, subject in stats.misses[domain]:
            print(f"  {sender[:40]:40}  {subject[:70]}")


def main() -> None:
    if not DB_PATH.exists():
        sys.exit(f"No database at {DB_PATH}. Run `npm run ingest` first.")
    with closing(connect()) as connection:
        messages = connection.execute(
            "SELECT from_addr, subject, substr(body_text, 1, 3000) AS body FROM messages"
        ).fetchall()
    print_report(domain_stats(messages), len(messages), sys.argv[1:])


if __name__ == "__main__":
    main()
