"""Top sender domains, and how many of their emails the keyword pre-filter matches."""

import sqlite3
from collections import Counter, defaultdict
import sys

from label import KEYWORDS, DB_PATH, sender_domain

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row  # lets us write r["from_addr"] instead of r[0]

rows = conn.execute(
    "SELECT from_addr, subject, substr(body_text, 1, 3000) AS body FROM messages"
).fetchall()

emails = Counter()   # domain -> number of emails
matches = Counter()  # domain -> number of those that match KEYWORDS
non_matches = defaultdict(list)  # domain -> (sender, subject) of emails with no keyword match

for r in rows:
    d = sender_domain(r["from_addr"])
    emails[d] += 1
    # Same text label.py samples on, so the counts mean the same thing.
    text = f"{r['from_addr']} {r['subject']} {r['body']}"
    if KEYWORDS.search(text):
        matches[d] += 1
    else:
        #non_matches[d].append(r["subject"])  
        non_matches[d].append((r["from_addr"] or "?", r["subject"] or "(no subject)"))
        

print(f"{'domain':40} {'emails':>7} {'keyword hits':>13}")
for d, n in emails.most_common(20):
    print(f"{d:40} {n:7} {matches[d]:13}")
print(f"\n{len(rows)} emails from {len(emails)} domains")

# Domains named on the command line: list the emails the keyword filter missed.
for d in sys.argv[1:]:
    d = d.lower()
    if d not in emails:
        print(f"\n{d}: no emails from this domain")
        continue
    print(f"\n{d}: {len(non_matches[d])} of {emails[d]} emails with no keyword match")
    for sender, subject in non_matches[d]:
        print(f"  {sender[:40]:40}  {subject[:70]}")