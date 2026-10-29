"""Hand-label emails. These labels are the ground truth every eval measures against.

    uv run py/label.py sample          # pick ~100 emails to label (once)
    uv run py/label.py                 # label them (resumable; q to quit)
    uv run py/label.py stats           # what your eval set covers

Stdlib only. Reads the same SQLite file the TypeScript sync writes.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("AGENT_DB", Path(__file__).resolve().parent.parent / "data" / "agent.db"))

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
    re.I,
)
MAX_PER_SENDER_DOMAIN = 4  # stop one job board from eating the whole sample


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        sys.exit(f"No database at {DB_PATH}. Run `npm run ingest` first.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE IF NOT EXISTS label_queue (
             position INTEGER PRIMARY KEY,
             account  TEXT NOT NULL,
             gmail_id TEXT NOT NULL,
             bucket   TEXT NOT NULL,          -- 'keyword' or 'random'
             UNIQUE (account, gmail_id))"""
    )
    return conn


def sender_domain(from_addr: str | None) -> str:
    m = re.search(r"@([\w.-]+)", from_addr or "")
    return m.group(1).lower() if m else "?"


# ---------------------------------------------------------------- sample

def cmd_sample(conn: sqlite3.Connection, n: int, seed: int, keyword_share: float, force: bool) -> None:
    if conn.execute("SELECT COUNT(*) FROM label_queue").fetchone()[0] and not force:
        sys.exit("A sample already exists. Re-sampling changes your eval set; use --force if you mean it.")
    rows = conn.execute(
        "SELECT account, gmail_id, from_addr, subject, substr(body_text, 1, 3000) AS body "
        "FROM messages ORDER BY account, gmail_id"
    ).fetchall()
    if not rows:
        sys.exit("No messages yet. Run `npm run ingest` first.")

    rng = random.Random(seed)  # fixed seed: the same DB always yields the same sample
    hits, rest = [], []
    for r in rows:
        text = f"{r['from_addr']} {r['subject']} {r['body']}"
        (hits if KEYWORDS.search(text) else rest).append(r)
    rng.shuffle(hits)
    rng.shuffle(rest)

    n_kw = min(round(n * keyword_share), len(hits))
    per_domain: Counter[str] = Counter()
    picked: list[tuple[sqlite3.Row, str]] = []
    for r in hits:
        if len(picked) >= n_kw:
            break
        d = sender_domain(r["from_addr"])
        if per_domain[d] < MAX_PER_SENDER_DOMAIN:
            per_domain[d] += 1
            picked.append((r, "keyword"))
    # Random fills the rest, including any keyword quota the domain cap left unused.
    picked += [(r, "random") for r in rest[: n - len(picked)]]
    rng.shuffle(picked)  # interleave so labeling order doesn't reveal the bucket

    conn.execute("DELETE FROM label_queue")
    conn.executemany(
        "INSERT INTO label_queue (position, account, gmail_id, bucket) VALUES (?, ?, ?, ?)",
        [(i, r["account"], r["gmail_id"], b) for i, (r, b) in enumerate(picked)],
    )
    conn.commit()
    b = Counter(bucket for _, bucket in picked)
    print(
        f"Queued {len(picked)} of {len(rows)} messages "
        f"({b['keyword']} keyword matches, {b['random']} random; {len(hits)} total keyword matches)."
    )


# ---------------------------------------------------------------- label

def cmd_label(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM label_queue").fetchone()[0]
    if not total:
        sys.exit("Nothing queued. Run: uv run py/label.py sample")
    skipped: set[int] = set()
    menu = (
        "  1 confirmation  2 outreach  3 assessment  4 interview invite  5 scheduling\n"
        "  6 info request  7 rejection  8 offer  9 other update  j job alert  n not recruiting\n"
        "  add ! for a note (e.g. 4!)   m more   s skip   z undo last   q quit"
    )
    while True:
        done = conn.execute(
            "SELECT COUNT(*) FROM label_queue q JOIN labels l USING (account, gmail_id)"
        ).fetchone()[0]
        row = next_unlabeled(conn, skipped)
        if row is None:
            print(f"\nDone: {done}/{total} labeled" + (f", {len(skipped)} skipped this session." if skipped else "."))
            return

        show(row, done, total)
        print(menu)
        choice = ask("> ")
        if choice == "q":
            return
        if choice == "s":
            skipped.add(row["position"])
            continue
        if choice == "z":
            undo_last(conn)
            continue
        if choice == "m":
            print(row["body_text"] or "")
            choice = ask("> ")
        wants_note = choice.endswith("!")
        category = CATEGORIES.get(choice.rstrip("!"))
        if category is None:
            print(f"  ? '{choice}' isn't an option")
            continue

        key = None if category in NO_KEY else ask_application_key(conn, row)
        note = ask("Note: ") or None if wants_note else None
        conn.execute(
            "INSERT OR REPLACE INTO labels (account, gmail_id, category, application_key, notes) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["account"], row["gmail_id"], category, key, note),
        )
        conn.commit()


def next_unlabeled(conn: sqlite3.Connection, skipped: set[int]) -> sqlite3.Row | None:
    for row in conn.execute(
        """SELECT q.position, q.bucket, m.* FROM label_queue q
           JOIN messages m USING (account, gmail_id)
           LEFT JOIN labels l USING (account, gmail_id)
           WHERE l.category IS NULL ORDER BY q.position"""
    ):
        if row["position"] not in skipped:
            return row
    return None


def show(row: sqlite3.Row, done: int, total: int) -> None:
    when = datetime.fromtimestamp(row["internal_date"] / 1000).strftime("%Y-%m-%d %H:%M")
    body = (row["body_text"] or "").strip()
    print("\n" + "=" * 78)
    print(f"[{done + 1}/{total}]  {row['account']}  {when}")
    print(f"From:    {row['from_addr']}")
    print(f"Subject: {row['subject']}")
    print("-" * 78)
    print(body[:1500] + ("\n  ... (m for more)" if len(body) > 1500 else ""))
    print("-" * 78)


def ask_application_key(conn: sqlite3.Connection, row: sqlite3.Row) -> str | None:
    """The same application must always get the exact same key, or the linking eval measures labeling noise."""
    keys = [r[0] for r in conn.execute(
        "SELECT application_key FROM labels WHERE application_key IS NOT NULL "
        "GROUP BY application_key ORDER BY MAX(rowid) DESC"
    )]
    haystack = f"{row['from_addr']} {row['subject']} {row['body_text']}".lower()
    suggestions = [k for k in keys if k.split(" | ")[0].lower() in haystack][:9] or keys[:5]

    print("Application as 'Company | Role' (role unknown: 'Company | ?'). Enter = none.")
    for i, k in enumerate(suggestions, 1):
        print(f"  {i}) {k}")
    while True:
        raw = ask("app> ")
        if not raw:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(suggestions):
            return suggestions[int(raw) - 1]
        if "|" not in raw:
            print("  needs a '|', e.g. 'Stripe | Backend Engineer' or 'Stripe | ?'")
            continue
        company, role = (part.strip() for part in raw.split("|", 1))
        # Reuse existing spelling of a company ("google" -> "Google").
        known = {k.split(" | ")[0].lower(): k.split(" | ")[0] for k in keys}
        company = known.get(company.lower(), company)
        return f"{company} | {role or '?'}"


def undo_last(conn: sqlite3.Connection) -> None:
    last = conn.execute(
        "SELECT l.rowid, m.subject FROM labels l JOIN messages m USING (account, gmail_id) "
        "ORDER BY l.rowid DESC LIMIT 1"
    ).fetchone()
    if last:
        conn.execute("DELETE FROM labels WHERE rowid = ?", (last["rowid"],))
        conn.commit()
        print(f"  undid: {last['subject']}")


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return "q"


# ---------------------------------------------------------------- stats

def cmd_stats(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """SELECT q.bucket, l.category, l.application_key FROM label_queue q
           LEFT JOIN labels l USING (account, gmail_id)"""
    ).fetchall()
    labeled = [r for r in rows if r["category"]]
    print(f"Labeled {len(labeled)}/{len(rows)}\n")

    by_cat: dict[str, Counter[str]] = defaultdict(Counter)
    for r in labeled:
        by_cat[r["category"]][r["bucket"]] += 1
    print(f"{'category':26} {'total':>5} {'keyword':>8} {'random':>7}")
    for cat in CATEGORIES.values():
        c = by_cat.get(cat, Counter())
        flag = "   <- none yet" if not c else ""
        print(f"{cat:26} {sum(c.values()):5} {c['keyword']:8} {c['random']:7}{flag}")

    # Recruiting mail the keyword filter missed. If this isn't ~0, the filter
    # has blind spots and the classifier must not rely on it.
    missed = [r for r in labeled if r["bucket"] == "random" and r["category"] not in NO_KEY]
    print(f"\nRecruiting emails found in the random bucket (keyword filter missed): {len(missed)}")

    keys = {r["application_key"] for r in labeled if r["application_key"]}
    per_company = Counter(k.split(" | ")[0] for k in keys)
    multi = {c: n for c, n in per_company.items() if n > 1}
    print(f"Distinct applications: {len(keys)} across {len(per_company)} companies")
    print(f"Companies with several applications (the hard linking case): {multi or 'none yet'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("sample", help="choose which emails to label")
    s.add_argument("--n", type=int, default=100)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--keyword-share", type=float, default=0.7)
    s.add_argument("--force", action="store_true")
    sub.add_parser("label", help="label queued emails (default)")
    sub.add_parser("stats", help="coverage of your labels")
    a = p.parse_args()

    conn = connect()
    if a.cmd == "sample":
        cmd_sample(conn, a.n, a.seed, a.keyword_share, a.force)
    elif a.cmd == "stats":
        cmd_stats(conn)
    else:
        cmd_label(conn)


if __name__ == "__main__":
    main()
