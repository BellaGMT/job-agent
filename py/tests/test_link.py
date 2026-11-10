"""Tests for the linking plumbing (link.py helpers, eval_link.py metrics). The exercise spec is separate."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data import Email, message_ids
from eval_link import merges, pairwise_scores, splits
from link import UnionFind, is_personal_sender, same_conversation, sender_address


def email(gmail_id="g1", account="me@gmail.com", thread_id="t1", rfc_message_id=None, references=()):
    return Email(
        account=account,
        gmail_id=gmail_id,
        from_addr="",
        to_addr="",
        subject="",
        sent_at=datetime(2026, 10, 1, tzinfo=UTC),
        body="",
        thread_id=thread_id,
        rfc_message_id=rfc_message_id,
        references=references,
    )


# --- union-find ---------------------------------------------------------------


def test_union_find_groups_transitively_and_numbers_by_first_item():
    groups = UnionFind(5)
    groups.union(3, 1)
    groups.union(1, 4)
    assert groups.same(3, 4) and not groups.same(0, 3)
    assert groups.group_ids() == [0, 1, 2, 1, 1]


def test_union_find_is_idempotent():
    groups = UnionFind(2)
    groups.union(0, 1)
    groups.union(1, 0)
    assert groups.group_ids() == [0, 0]


# --- senders ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_addr", "expected"),
    [
        ("Chris Haener <CHaener@Tesla.com>", "chaener@tesla.com"),
        ("pcorn@tesla.com", "pcorn@tesla.com"),
        ('"Doe, Jane" <jane@x.com>', "jane@x.com"),
    ],
)
def test_sender_address(from_addr, expected):
    assert sender_address(from_addr) == expected


@pytest.mark.parametrize(
    ("address", "personal"),
    [
        ("chaener@tesla.com", True),
        ("hrishikesh@acme.com", True),  # starts with "hr" but is a name
        ("noreply@mail.amazon.jobs", False),
        ("no-reply@greenhouse.io", False),
        ("stripe@greenhouse.io", False),  # a person's name on a platform still speaks for many roles
        ("recruiting@stripe.com", False),
        ("careers@acme.com", False),
        ("notifications@acme.myworkday.com", False),
        ("jobs-noreply@linkedin.com", False),
        ("", False),
        ("not-an-address", False),
    ],
)
def test_is_personal_sender(address, personal):
    assert is_personal_sender(address) is personal


# --- conversations ------------------------------------------------------------


def test_same_thread_counts_only_within_one_inbox():
    assert same_conversation(email("a"), email("b"))
    assert not same_conversation(email("a"), email("b", account="other@gmail.com"))
    assert not same_conversation(email("a", thread_id=""), email("b", thread_id=""))


def test_message_ids_connect_across_inboxes():
    original = email("a", thread_id="t1", rfc_message_id="<m1@x>")
    reply = email("b", account="other@gmail.com", thread_id="t2", references=("<m0@x>", "<m1@x>"))
    sibling = email("c", account="other@gmail.com", thread_id="t3", references=("<m0@x>",))
    assert same_conversation(original, reply)
    assert same_conversation(reply, sibling)  # both reply into the same chain
    assert not same_conversation(original, sibling)


def test_message_ids_parses_headers():
    assert message_ids("<a@x> <b@y>\n\t<a@x>") == ("<a@x>", "<b@y>")
    assert message_ids("") == ()


# --- metrics ------------------------------------------------------------------


def test_perfect_linking():
    scores = pairwise_scores(["A", "A", "B"], [7, 7, 3])
    assert (scores.true_pairs, scores.linked_pairs, scores.correct_pairs) == (1, 1, 1)
    assert scores.precision == scores.recall == 1.0


def test_a_split_lowers_recall_and_a_merge_lowers_precision():
    split = pairwise_scores(["A", "A", "A"], [0, 0, 1])  # 3 true pairs, 1 linked
    assert (split.precision, split.recall) == (1.0, 1 / 3)
    merged = pairwise_scores(["A", "B", "B"], [0, 0, 0])  # 1 true pair, 3 linked
    assert (merged.precision, merged.recall) == (1 / 3, 1.0)


def test_no_pairs_gives_no_score():
    scores = pairwise_scores(["A", "B"], [0, 1])
    assert scores.precision is None and scores.recall is None


def test_splits_and_merges_list_positions():
    keys = ["A", "A", "B", "C"]
    ids = [0, 1, 2, 2]
    assert splits(keys, ids) == {"A": [[0], [1]]}
    assert merges(keys, ids) == {2: [[2], [3]]}
