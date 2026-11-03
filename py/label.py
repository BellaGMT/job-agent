"""Hand-label emails. These labels are the ground truth every eval measures against.

    uv run python label.py sample          # pick ~100 emails to label (once)
    uv run python label.py                 # label them (resumable; q to quit)
    uv run python label.py stats           # what your eval set covers
    uv run python label.py search offer    # find rare cases and add them to the queue
    uv run python label.py fix SpaceX      # re-label an email already labeled
    uv run python label.py rename-key "Old | Key" "New | Key"

Stdlib only. Reads the same SQLite file the TypeScript sync writes.
"""

from __future__ import annotations

import argparse
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Literal

from db import DB_PATH, connect

# One email, one category. "Needs follow-up" is deliberately NOT a category:
# it depends on what happened after the email (state), not on the email itself.
CATEGORIES = {
    "1": "application_confirmation",
    "2": "recruiter_outreach",
    "3": "assessment_invite",
    "4": "interview_invite",
    "5": "interview_scheduling",  # confirmations, reschedules, logistics
    "6": "info_request",  # documents, forms, questions for you
    "7": "rejection",
    "8": "offer",
    "9": "recruiting_update",  # any other update about a specific opportunity
    "j": "job_alert",  # job boards, newsletters: recruiting-ish but about no application
    "n": "not_recruiting",
}
# Categories about a specific opportunity get an application key.
NO_KEY = {"job_alert", "not_recruiting"}

# A cheap pre-filter so the sample isn't 95% newsletters. Its blind spots are
# exactly why 30% of the sample is drawn at random from what it did NOT match.
KEYWORDS = re.compile(
    r"\b(appl(y|ied|ying|ication)|interview|recruit\w*|candida\w+|position|role|"
    r"opportunit\w+|assessment|coding (challenge|test)|offer|hiring|talent|"
    r"next steps|move forward|unfortunately|requisition)\b"
    r"|greenhouse|lever\.co|workday|ashbyhq|smartrecruiters|icims|jobvite|taleo|"
    r"successfactors|workable|hackerrank|codesignal|hirevue|karat|wellfound|handshake",
    re.IGNORECASE,
)
MAX_PER_SENDER_DOMAIN = 4  # stop one job board from eating the whole sample
BODY_PREVIEW_CHARS = 1500

Bucket = Literal["keyword", "random", "targeted"]
PromptOutcome = Literal["saved", "skip", "undo", "quit", "invalid"]
# What the application-key prompt returns besides a key: go back, or quit.
KEY_BACK = "b"
KEY_QUIT = "q"


# ---------------------------------------------------------------- domains


def base_domain(domain: str | None) -> str:
    """'notifications.joinhandshake.com' -> 'joinhandshake.com'. Naive: 'x.co.uk' -> 'co.uk'."""
    labels = (domain or "").split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else "?"


def sender_domain(from_address: str | None) -> str:
    address_match = re.search(r"@([\w.-]+)", from_address or "")
    return base_domain(address_match.group(1).lower() if address_match else "?")


# ---------------------------------------------------------------- sample


def matches_keywords(message: sqlite3.Row) -> bool:
    return bool(KEYWORDS.search(f"{message['from_addr']} {message['subject']} {message['body']}"))


def choose_sample(
    messages: Sequence[sqlite3.Row], sample_size: int, seed: int, keyword_share: float
) -> list[tuple[sqlite3.Row, Bucket]]:
    """Pick the eval sample: mostly keyword matches (capped per sender domain), the rest at random.

    Deterministic for a given seed and input order. Rows need from_addr, subject and body.
    """
    rng = random.Random(seed)
    keyword_matches = [message for message in messages if matches_keywords(message)]
    others = [message for message in messages if not matches_keywords(message)]
    rng.shuffle(keyword_matches)
    rng.shuffle(others)

    keyword_quota = min(round(sample_size * keyword_share), len(keyword_matches))
    picked_per_domain: Counter[str] = Counter()
    picked: list[tuple[sqlite3.Row, Bucket]] = []
    for message in keyword_matches:
        if len(picked) >= keyword_quota:
            break
        domain = sender_domain(message["from_addr"])
        if picked_per_domain[domain] < MAX_PER_SENDER_DOMAIN:
            picked_per_domain[domain] += 1
            picked.append((message, "keyword"))
    # Random fills the rest, including any keyword quota the domain cap left unused.
    picked += [(message, "random") for message in others[: sample_size - len(picked)]]
    rng.shuffle(picked)  # interleave so labeling order doesn't reveal the bucket
    return picked


def cmd_sample(connection: sqlite3.Connection, sample_size: int, seed: int, keyword_share: float, force: bool) -> None:
    if queued_count(connection) and not force:
        sys.exit("A sample already exists. Re-sampling changes your eval set; use --force if you mean it.")
    messages = connection.execute(
        "SELECT account, gmail_id, from_addr, subject, substr(body_text, 1, 3000) AS body "
        "FROM messages ORDER BY account, gmail_id"
    ).fetchall()
    if not messages:
        sys.exit("No messages yet. Run `npm run ingest` first.")

    picked = choose_sample(messages, sample_size, seed, keyword_share)
    connection.execute("DELETE FROM label_queue")
    connection.executemany(
        "INSERT INTO label_queue (position, account, gmail_id, bucket) VALUES (?, ?, ?, ?)",
        [(position, message["account"], message["gmail_id"], bucket) for position, (message, bucket) in enumerate(picked)],
    )
    connection.commit()
    bucket_counts = Counter(bucket for _, bucket in picked)
    keyword_total = sum(1 for message in messages if matches_keywords(message))
    print(
        f"Queued {len(picked)} of {len(messages)} messages ({bucket_counts['keyword']} keyword matches, "
        f"{bucket_counts['random']} random; {keyword_total} total keyword matches)."
    )


def queued_count(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM label_queue").fetchone()[0])


# ---------------------------------------------------------------- label

MENU = (
    "  1 confirmation  2 outreach  3 assessment  4 interview invite  5 scheduling\n"
    "  6 info request  7 rejection  8 offer  9 other update  j job alert  n not recruiting\n"
    "  add ! for a note (e.g. 4!)   m more   s skip   z undo last   q quit"
)


def cmd_label(connection: sqlite3.Connection) -> None:
    queue_size = queued_count(connection)
    if not queue_size:
        sys.exit("Nothing queued. Run: uv run python label.py sample")
    skipped_positions: set[int] = set()
    while True:
        labeled_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM label_queue q JOIN labels l USING (account, gmail_id)"
            ).fetchone()[0]
        )
        email = next_unlabeled(connection, skipped_positions)
        if email is None:
            skipped_note = f", {len(skipped_positions)} skipped this session." if skipped_positions else "."
            print(f"\nDone: {labeled_count}/{queue_size} labeled{skipped_note}")
            return
        outcome = prompt_label(connection, email, f"[{labeled_count + 1}/{queue_size}]  {email['bucket']}")
        if outcome == "quit":
            return
        if outcome == "skip":
            skipped_positions.add(email["position"])


def prompt_label(connection: sqlite3.Connection, email: sqlite3.Row, header: str) -> PromptOutcome:
    """Show one email, ask for its label, save it."""
    show(email, header)
    print(MENU)
    choice = ask("> ")
    if choice == "q":
        return "quit"
    if choice == "s":
        return "skip"
    if choice == "z":
        undo_last(connection)
        return "undo"
    if choice == "m":
        print(email["body_text"] or "")
        choice = ask("> ")
    wants_note = choice.endswith("!")
    category = CATEGORIES.get(choice.rstrip("!"))
    if category is None:
        print(f"  ? '{choice}' isn't an option")
        return "invalid"

    application_key = None if category in NO_KEY else ask_application_key(connection, email)
    if application_key == KEY_BACK:
        return "invalid"  # nothing saved; the same email is shown again
    if application_key == KEY_QUIT:
        return "quit"
    note = (ask("Note: ") or None) if wants_note else None
    save_label(connection, email, category, application_key, note)
    return "saved"


def save_label(
    connection: sqlite3.Connection, email: sqlite3.Row, category: str, application_key: str | None, note: str | None
) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO labels (account, gmail_id, category, application_key, notes) VALUES (?, ?, ?, ?, ?)",
        (email["account"], email["gmail_id"], category, application_key, note),
    )
    connection.commit()


def next_unlabeled(connection: sqlite3.Connection, skipped_positions: set[int]) -> sqlite3.Row | None:
    candidate: sqlite3.Row
    for candidate in connection.execute(
        """SELECT q.position, q.bucket, m.* FROM label_queue q
           JOIN messages m USING (account, gmail_id)
           LEFT JOIN labels l USING (account, gmail_id)
           WHERE l.category IS NULL ORDER BY q.position"""
    ):
        if candidate["position"] not in skipped_positions:
            return candidate
    return None


def show(email: sqlite3.Row, header: str) -> None:
    received = datetime.fromtimestamp(email["internal_date"] / 1000).strftime("%Y-%m-%d %H:%M")
    body = (email["body_text"] or "").strip()
    print("\n" + "=" * 78)
    print(f"{header}  {email['account']}  {received}")
    print(f"From:    {email['from_addr']}")
    print(f"Subject: {email['subject']}")
    print("-" * 78)
    print(body[:BODY_PREVIEW_CHARS] + ("\n  ... (m for more)" if len(body) > BODY_PREVIEW_CHARS else ""))
    print("-" * 78)


def existing_keys(connection: sqlite3.Connection, excluding: str | None = None) -> list[str]:
    """Application keys already in use, most recently used first."""
    rows = connection.execute(
        "SELECT application_key FROM labels WHERE application_key IS NOT NULL AND application_key IS NOT ? "
        "GROUP BY application_key ORDER BY MAX(rowid) DESC",
        (excluding,),
    )
    return [str(row[0]) for row in rows]


def suggest_keys(known_keys: list[str], email: sqlite3.Row) -> list[str]:
    """Keys whose company appears in the email; otherwise the most recent ones."""
    haystack = f"{email['from_addr']} {email['subject']} {email['body_text']}".lower()
    by_company = [key for key in known_keys if company_of(key).lower() in haystack]
    return by_company[:9] or known_keys[:5]


def ask_application_key(connection: sqlite3.Connection, email: sqlite3.Row) -> str | None:
    """The same application must always get the exact same key, or the linking eval measures labeling noise.

    Returns a key, None (no key), KEY_BACK or KEY_QUIT.
    """
    known_keys = existing_keys(connection)
    suggestions = suggest_keys(known_keys, email)
    print("Application as 'Company | Role' (role unknown: 'Company | ?'). Enter = none, b = back, q = quit.")
    for number, key in enumerate(suggestions, 1):
        print(f"  {number}) {key}")
    while True:
        answer = ask("app> ")
        if not answer:
            return None
        if answer in (KEY_BACK, KEY_QUIT):
            return answer
        if answer.isdigit() and 1 <= int(answer) <= len(suggestions):
            return suggestions[int(answer) - 1]
        if "|" not in answer:
            print("  needs a '|', e.g. 'Stripe | Backend Engineer' or 'Stripe | ?'")
            continue
        return normalize_key(answer, known_keys)


def company_of(application_key: str) -> str:
    return application_key.split(" | ")[0]


def normalize_key(raw_key: str, known_keys: Iterable[str]) -> str:
    """'google|  Backend  Eng ' -> 'Google | Backend Eng' (reusing a known company spelling)."""
    company, role = (" ".join(part.split()) for part in raw_key.split("|", 1))
    known_spellings = {company_of(key).lower(): company_of(key) for key in known_keys}
    company = known_spellings.get(company.lower(), company)
    return f"{company} | {role or '?'}"


def undo_last(connection: sqlite3.Connection) -> None:
    last_label = connection.execute(
        "SELECT l.rowid, m.subject FROM labels l JOIN messages m USING (account, gmail_id) ORDER BY l.rowid DESC LIMIT 1"
    ).fetchone()
    if last_label:
        connection.execute("DELETE FROM labels WHERE rowid = ?", (last_label["rowid"],))
        connection.commit()
        print(f"  undid: {last_label['subject']}")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


# ---------------------------------------------------------------- targeted additions & fixes


def cmd_search(connection: sqlite3.Connection, search_text: str, limit: int) -> None:
    """Find rare cases (offers, scheduling...) and add them to the queue as 'targeted'.

    Targeted emails are hand-picked, so they're biased: they fill coverage gaps,
    and stats reports them separately from the sampled buckets.
    """
    pattern = f"%{search_text}%"
    matches = connection.execute(
        """SELECT m.account, m.gmail_id, m.internal_date, m.from_addr, m.subject FROM messages m
           LEFT JOIN label_queue q USING (account, gmail_id)
           WHERE q.gmail_id IS NULL
             AND (m.subject LIKE ? OR m.from_addr LIKE ? OR m.body_text LIKE ?)
           ORDER BY m.internal_date DESC LIMIT ?""",
        (pattern, pattern, pattern, limit),
    ).fetchall()
    if not matches:
        print(f"No unqueued emails match '{search_text}'.")
        return
    for number, match in enumerate(matches, 1):
        received = datetime.fromtimestamp(match["internal_date"] / 1000).strftime("%Y-%m-%d")
        print(f"{number:3}) {received}  {(match['from_addr'] or '')[:32]:32}  {(match['subject'] or '')[:70]}")
    answers = ask("Add which? (e.g. 1 4 7, Enter = none) ").replace(",", " ").split()
    chosen = [matches[int(answer) - 1] for answer in answers if answer.isdigit() and 1 <= int(answer) <= len(matches)]
    added = add_targeted(connection, chosen)
    print(f"Added {added}. Run `label` to label them.")


def add_targeted(connection: sqlite3.Connection, emails: Sequence[sqlite3.Row]) -> int:
    """Append emails to the end of the queue; already-queued ones are ignored. Returns how many were added."""
    next_position = int(connection.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM label_queue").fetchone()[0])
    before = queued_count(connection)
    connection.executemany(
        "INSERT OR IGNORE INTO label_queue (position, account, gmail_id, bucket) VALUES (?, ?, ?, 'targeted')",
        [(next_position + offset, email["account"], email["gmail_id"]) for offset, email in enumerate(emails)],
    )
    connection.commit()
    return queued_count(connection) - before


def cmd_fix(connection: sqlite3.Connection, search_text: str) -> None:
    """Re-label an already-labeled email, found by subject, sender or application key."""
    pattern = f"%{search_text}%"
    matches = connection.execute(
        """SELECT q.position, q.bucket, m.*, l.category, l.application_key FROM labels l
           JOIN messages m USING (account, gmail_id)
           JOIN label_queue q USING (account, gmail_id)
           WHERE m.subject LIKE ? OR m.from_addr LIKE ? OR l.application_key LIKE ?
           ORDER BY m.internal_date""",
        (pattern, pattern, pattern),
    ).fetchall()
    if not matches:
        print(f"No labeled emails match '{search_text}'.")
        return
    for number, match in enumerate(matches, 1):
        received = datetime.fromtimestamp(match["internal_date"] / 1000).strftime("%Y-%m-%d %H:%M")
        subject = (match["subject"] or "")[:40]
        print(f"{number:3}) {received}  {match['category']:24}  {subject:40}  {match['application_key'] or ''}")
    answer = ask("Re-label which? (number, Enter = none) ")
    if answer.isdigit() and 1 <= int(answer) <= len(matches):
        email = matches[int(answer) - 1]
        while prompt_label(connection, email, f"[fix, was {email['category']}]") == "invalid":
            pass


def rename_key(connection: sqlite3.Connection, old_key: str, new_key: str) -> tuple[int, str, bool]:
    """Rename an application key everywhere. Returns (labels changed, normalized new key, merged into existing key)."""
    usage_count = int(
        connection.execute("SELECT COUNT(*) FROM labels WHERE application_key = ?", (old_key,)).fetchone()[0]
    )
    if not usage_count:
        raise ValueError(f"No labels use the key '{old_key}'. Check `stats` or `fix` for the exact spelling.")
    if "|" not in new_key:
        raise ValueError("New key needs a '|', e.g. 'GitHub | Software Engineer I, Secret Scanning'.")
    other_keys = existing_keys(connection, excluding=old_key)
    normalized = normalize_key(new_key, other_keys)
    connection.execute("UPDATE labels SET application_key = ? WHERE application_key = ?", (normalized, old_key))
    connection.commit()
    return usage_count, normalized, normalized in other_keys


def cmd_rename_key(connection: sqlite3.Connection, old_key: str, new_key: str) -> None:
    try:
        usage_count, normalized, merged = rename_key(connection, old_key, new_key)
    except ValueError as error:
        sys.exit(str(error))
    merged_note = " (merged with an existing key)" if merged else ""
    print(f"Renamed {usage_count} label(s): '{old_key}' -> '{normalized}'{merged_note}")


# ---------------------------------------------------------------- stats


def near_duplicate_keys(keys: Iterable[str]) -> list[list[str]]:
    """Groups of keys that differ only in case, spacing or punctuation: usually typos."""
    groups: defaultdict[str, list[str]] = defaultdict(list)
    for key in sorted(set(keys)):
        groups[re.sub(r"[^a-z0-9]", "", key.lower())].append(key)
    return [group for group in groups.values() if len(group) > 1]


def cmd_stats(connection: sqlite3.Connection) -> None:
    queue = connection.execute(
        "SELECT q.bucket, l.category, l.application_key FROM label_queue q LEFT JOIN labels l USING (account, gmail_id)"
    ).fetchall()
    labeled = [entry for entry in queue if entry["category"]]
    print(f"Labeled {len(labeled)}/{len(queue)}\n")
    print_category_table(labeled)

    # Recruiting mail the keyword filter missed. If this isn't ~0, the filter
    # has blind spots and the classifier must not rely on it.
    missed = [entry for entry in labeled if entry["bucket"] == "random" and entry["category"] not in NO_KEY]
    print(f"\nRecruiting emails found in the random bucket (keyword filter missed): {len(missed)}")

    keys = {entry["application_key"] for entry in labeled if entry["application_key"]}
    applications_per_company = Counter(company_of(key) for key in keys)
    multi_application = {company: count for company, count in applications_per_company.items() if count > 1}
    print(f"Distinct applications: {len(keys)} across {len(applications_per_company)} companies")
    duplicates = near_duplicate_keys(keys)
    if duplicates:
        print(f"Keys that differ only in case/spacing/punctuation (fix with rename-key): {duplicates}")
    print(f"Companies with several applications (the hard linking case): {multi_application or 'none yet'}")


def print_category_table(labeled: Sequence[sqlite3.Row]) -> None:
    by_category: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for entry in labeled:
        by_category[entry["category"]][entry["bucket"]] += 1
    print(f"{'category':26} {'total':>5} {'keyword':>8} {'random':>7} {'targeted':>9}")
    for category in CATEGORIES.values():
        bucket_counts = by_category.get(category, Counter())
        total = sum(bucket_counts.values())
        flag = "   <- none yet" if not total else "   <- thin" if total < 3 else ""
        print(
            f"{category:26} {total:5} {bucket_counts['keyword']:8} {bucket_counts['random']:7} "
            f"{bucket_counts['targeted']:9}{flag}"
        )


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command")
    sample = commands.add_parser("sample", help="choose which emails to label")
    sample.add_argument("--n", dest="sample_size", type=int, default=100)
    sample.add_argument("--seed", type=int, default=7)
    sample.add_argument("--keyword-share", type=float, default=0.7)
    sample.add_argument("--force", action="store_true")
    commands.add_parser("label", help="label queued emails (default)")
    commands.add_parser("stats", help="coverage of your labels")
    search = commands.add_parser("search", help="find emails by text and add them to the queue")
    search.add_argument("text")
    search.add_argument("--limit", type=int, default=30)
    fix = commands.add_parser("fix", help="re-label an already-labeled email")
    fix.add_argument("text")
    rename = commands.add_parser("rename-key", help="rename an application key everywhere")
    rename.add_argument("old")
    rename.add_argument("new")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not DB_PATH.exists():
        sys.exit(f"No database at {DB_PATH}. Run `npm run ingest` first.")
    connection = connect()
    try:
        if args.command == "sample":
            cmd_sample(connection, args.sample_size, args.seed, args.keyword_share, args.force)
        elif args.command == "stats":
            cmd_stats(connection)
        elif args.command == "search":
            cmd_search(connection, args.text, args.limit)
        elif args.command == "fix":
            cmd_fix(connection, args.text)
        elif args.command == "rename-key":
            cmd_rename_key(connection, args.old, args.new)
        else:
            cmd_label(connection)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
