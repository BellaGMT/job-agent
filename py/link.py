"""Link recruiting emails to the application each one belongs to (entity resolution).

The classifier says what an email is; this module says which application it is
about. One application is one role at one company, so two roles at the same
company are two applications, and one application usually spans several emails
(confirmation, scheduling, rejection) from several senders and threads.

`link` groups emails with deterministic rules, strongest evidence first. No
model call: the classifier already extracted company, role and requisition id,
and rules are cheap, explainable and testable. Emails the rules can't place stay
in their own group rather than being guessed into one; a later step can send
those to a model or to a review queue.

`normalize_company`, `normalize_role` and `link` are a maintainer exercise
(see CLAUDE.md); tests/test_link_exercise.py specifies them.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from data import Email

# Senders that speak for many applications at once: shared mailboxes and
# applicant tracking systems. Matching on them would merge unrelated applications.
AUTOMATED_LOCAL_PART = re.compile(
    r"^(no-?reply|do-?not-?reply|notifications?|alerts?|mailer|info|jobs|careers|talent|recruiting|hr|hello)\b"
    r"|no-?reply",
    re.IGNORECASE,
)
ATS_DOMAINS = (
    "greenhouse.io",
    "lever.co",
    "myworkday.com",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "icims.com",
    "jobvite.com",
    "taleo.net",
    "successfactors.com",
    "workable.com",
    "linkedin.com",
    "indeed.com",
    "joinhandshake.com",
)

# A comprehensive Python list of global corporate and business suffixes
COMPANY_SUFFIXES = (
    # North America
    "abc", "co", "corp", "corporation", "com", "inc", "lc", "llc", "llp", "lp", "ltd", "pa", "pc", "ulc",
    
    # United Kingdom & Commonwealth
    "cic", "govt", "ltd", "nl", "pty", "plc", "pte",
    
    # Europe (Continental)
    "ab", "a/s", "ag", "aps", "asa", "as", "bv.",  "eurl", "gmbh", "kga a", "nv",
    "oy", "oyj", "sa", "sarl", "srl", "spa",
    
    # Asia-Pacific
   "gk.", "hk.", "kk", "yk"
)

_ADDRESS = re.compile(r"<([^<>]+)>")


@dataclass(frozen=True)
class Candidate:
    """One recruiting email and the fields the classifier extracted from it."""

    email: Email
    company: str | None
    role: str | None
    requisition_id: str | None


# ------------------------------------------------------------------ provided


class UnionFind:
    """Groups items 0..size-1. `union(a, b)` puts a and b in one group, merging their groups."""

    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        """The representative of the item's group (path halving keeps lookups near O(1))."""
        while self._parent[item] != item:
            self._parent[item] = self._parent[self._parent[item]]
            item = self._parent[item]
        return item

    def union(self, first: int, second: int) -> None:
        self._parent[self.find(first)] = self.find(second)

    def same(self, first: int, second: int) -> bool:
        return self.find(first) == self.find(second)

    def group_ids(self) -> list[int]:
        """One id per item: 0, 1, 2... numbered by each group's first item."""
        ids: dict[int, int] = {}
        return [ids.setdefault(self.find(item), len(ids)) for item in range(len(self._parent))]


def sender_address(from_addr: str) -> str:
    """'Chris <CHaener@tesla.com>' -> 'chaener@tesla.com'."""
    match = _ADDRESS.search(from_addr)
    return (match.group(1) if match else from_addr).strip().lower()


def is_personal_sender(address: str) -> bool:
    """True for a person's mailbox (a recruiter), False for shared mailboxes and platforms."""
    local_part, _, domain = address.partition("@")
    if not local_part or not domain:
        return False
    on_platform = any(domain == ats or domain.endswith(f".{ats}") for ats in ATS_DOMAINS)
    return not on_platform and not AUTOMATED_LOCAL_PART.search(local_part)


def same_conversation(first: Email, second: Email) -> bool:
    """Same Gmail thread in the same inbox, or connected by Message-ID headers (works across inboxes)."""
    if first.account == second.account and first.thread_id and first.thread_id == second.thread_id:
        return True
    first_ids = set(first.references) | ({first.rfc_message_id} if first.rfc_message_id else set())
    second_ids = set(second.references) | ({second.rfc_message_id} if second.rfc_message_id else set())
    return bool(first_ids & second_ids)


# ------------------------------------------------------------------ your part


def normalize_company(name: str | None) -> str | None:
    """A comparable form of a company name, or None if there is none.

    "Tesla, Inc." and "TESLA" both become "tesla". See the spec for the rules.
    """
    name = re.split(r'[\s.,]+', name.lower().strip(" .,")) if name else None
    name = [word for word in name if word] if name else None
    while name and len(name) > 1 and name[-1] in COMPANY_SUFFIXES:
        name.pop()
    return " ".join(name) if name else None


def normalize_role(title: str | None) -> str | None:
    """A comparable form of a job title, or None if there is none.

    "Sr. Software Engineer (ID: 123)" becomes "senior software engineer".
    """
    if not title:
        return None
    pattern = r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}"
    title = re.sub(pattern, '', title.lower().strip(" .,"))
    title = re.sub(r"[^a-zA-Z0-9\s]", " ", title)
    title = re.sub(r"\bSr\b", "senior", title, flags=re.IGNORECASE)
    title = re.sub(r"\bJr\b", "junior", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip()
    #title = re.split(r'[\s.,]+', re.sub(pattern, '', title.lower().strip(" .,"))) if title else None
    # title = [word for word in title if word] if title else None
    # while title and len(title) > 1 and title[-1] in ROLE_SUFFIXES:
    #     title.pop()
    return title if title else None


def link(candidates: Sequence[Candidate]) -> list[int]:
    """One application id per candidate, in the same order. Equal ids mean the same application.

    Rules, strongest evidence first (the spec has a test for each):
      1. Same conversation (`same_conversation`).
      2. Same company and same requisition id.
      3. Same company and same role, unless their requisition ids differ
         (one title can be open on several teams, each with its own id).
      4. Same personal sender (`is_personal_sender`), unless the two emails
         name different companies, roles or requisition ids.
      5. An email still alone after rules 1-4, with a company but no role and
         no requisition id, joins that company's application if the company
         has exactly one (one group containing an email with that company and
         a role or requisition id). Otherwise it stays alone: never guess.

    Companies and roles are compared normalized. Any field compares only when
    both emails have it: two unknowns are not a match, and one unknown is not
    a difference.
    """
    raise NotImplementedError("Exercise: implement to pass tests/test_link_exercise.py")
