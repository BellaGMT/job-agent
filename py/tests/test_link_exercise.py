"""Spec for the linking exercise in link.py: normalize_company, normalize_role and link.

Run just these with: uv run pytest tests/test_link_exercise.py
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data import Email
from link import Candidate, link, normalize_company, normalize_role

# --- normalize_company --------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Tesla", "tesla"),
        ("TESLA", "tesla"),  # case doesn't matter
        ("  Tesla  ", "tesla"),  # neither does surrounding space
        ("Tesla, Inc.", "tesla"),  # legal suffixes go, with their punctuation
        ("Stripe Inc", "stripe"),
        ("Acme Holdings, LLC", "acme holdings"),
        ("Globex Corporation", "globex"),
        ("Initech Corp.", "initech"),
        ("Umbrella Ltd", "umbrella"),
        ("Amazon.com", "amazon"),  # ".com" is a suffix too
        ("Amazon.com, Inc.", "amazon"),  # several suffixes in a row all go
        ("Johnson & Johnson", "johnson & johnson"),  # & is part of the name
        ("Meridian   Labs", "meridian labs"),  # runs of spaces collapse to one
    ],
)
def test_normalize_company(name, expected):
    assert normalize_company(name) == expected


@pytest.mark.parametrize("name", [None, "", "   ", ",."])
def test_normalize_company_without_a_name_is_none(name):
    assert normalize_company(name) is None


def test_a_suffix_alone_is_kept_as_the_name():
    assert normalize_company("Corp") == "corp"  # stripping it would leave nothing


# --- normalize_role -----------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Software Engineer", "software engineer"),
        ("  SOFTWARE   Engineer ", "software engineer"),
        ("Robotics - Software Development Engineer (ID:10452115)", "robotics software development engineer"),
        ("Backend Engineer [Remote]", "backend engineer"),  # bracketed asides go, like parentheses
        ("Software Engineer, People Programs", "software engineer people programs"),
        ("Software Engineer - People Programs", "software engineer people programs"),
        ("Sr. Software Engineer", "senior software engineer"),
        ("Sr Data Scientist", "senior data scientist"),
        ("Jr. Developer", "junior developer"),
        ("Software Engineer II", "software engineer ii"),  # levels are meaningful: keep them
        ("Front-End/UI Engineer", "front end ui engineer"),
    ],
)
def test_normalize_role(title, expected):
    assert normalize_role(title) == expected


@pytest.mark.parametrize("title", [None, "", "  ", "(ID: 123)"])
def test_normalize_role_without_a_title_is_none(title):
    assert normalize_role(title) is None


# --- link ---------------------------------------------------------------------


def candidate(
    gmail_id,
    company=None,
    role=None,
    requisition_id=None,
    sender="noreply@ats.example",
    thread_id=None,
    rfc_message_id=None,
    references=(),
    account="me@gmail.com",
):
    email = Email(
        account=account,
        gmail_id=gmail_id,
        from_addr=f"Someone <{sender}>",
        to_addr=account,
        subject="",
        sent_at=datetime(2026, 10, 1, tzinfo=UTC),
        body="",
        thread_id=thread_id or f"thread-{gmail_id}",
        rfc_message_id=rfc_message_id,
        references=references,
    )
    return Candidate(email, company, role, requisition_id)


def groups(candidates, ids):
    """The partition as sets of gmail ids, so tests don't depend on which numbers `link` picks."""
    assert len(ids) == len(candidates), "link must return one id per candidate"
    by_id: dict[int, set[str]] = {}
    for item, application_id in zip(candidates, ids, strict=True):
        by_id.setdefault(application_id, set()).add(item.email.gmail_id)
    return sorted(sorted(group) for group in by_id.values())


def test_empty_input():
    assert link([]) == []


def test_unrelated_emails_stay_apart():
    emails = [candidate("a", "Stripe", "Backend Engineer"), candidate("b", "Tesla", "Software Engineer")]
    assert groups(emails, link(emails)) == [["a"], ["b"]]


def test_rule_1_same_thread_in_one_inbox():
    emails = [
        candidate("invite", "Tesla", "Software Engineer", thread_id="t1"),
        candidate("reply", None, None, thread_id="t1"),  # "Thursday works!" names nothing
    ]
    assert groups(emails, link(emails)) == [["invite", "reply"]]


def test_rule_1_thread_ids_only_count_within_one_inbox():
    emails = [
        candidate("a", "Stripe", "Backend Engineer", thread_id="t1", account="me@gmail.com"),
        candidate("b", "Tesla", "Software Engineer", thread_id="t1", account="other@gmail.com"),
    ]
    assert groups(emails, link(emails)) == [["a"], ["b"]]


def test_rule_1_message_id_headers_connect_inboxes():
    emails = [
        candidate("sent", "Tesla", "Software Engineer", rfc_message_id="<m1@x>", account="me@gmail.com"),
        candidate("answer", None, None, references=("<m1@x>",), account="other@gmail.com"),
    ]
    assert groups(emails, link(emails)) == [["answer", "sent"]]


def test_rule_2_same_company_and_requisition_id():
    emails = [
        candidate("confirm", "Amazon", "Robotics SDE", requisition_id="10452115"),
        candidate("reject", "Amazon.com, Inc.", "Software Development Engineer", requisition_id="10452115"),
        candidate("other", "Amazon", "Robotics SDE", requisition_id="99999"),
    ]
    # Same id despite differently written titles; a different id is a different application.
    assert groups(emails, link(emails)) == [["confirm", "reject"], ["other"]]


def test_rule_3_same_company_and_role_after_normalizing():
    emails = [
        candidate("a", "Tesla, Inc.", "Software Engineer, People Programs"),
        candidate("b", "TESLA", "Software Engineer - People Programs"),
        candidate("c", "Tesla", "Data Engineer"),  # same company, different role
        candidate("d", "Stripe", "Software Engineer, People Programs"),  # same role, different company
    ]
    assert groups(emails, link(emails)) == [["a", "b"], ["c"], ["d"]]


def test_rule_4_same_recruiter_links_an_email_that_names_no_role():
    emails = [
        candidate("update", "Tesla", "Software Engineer", sender="chaener@tesla.com"),
        candidate("calendar", "Tesla", None, sender="chaener@tesla.com"),  # "Chris to call Milca"
    ]
    assert groups(emails, link(emails)) == [["calendar", "update"]]


def test_rule_4_same_recruiter_two_roles_are_two_applications():
    emails = [
        candidate("a", "Tesla", "Software Engineer", sender="pcorn@tesla.com"),
        candidate("b", "Tesla", "Data Engineer", sender="pcorn@tesla.com"),
    ]
    assert groups(emails, link(emails)) == [["a"], ["b"]]


def test_rule_4_ignores_shared_and_platform_senders():
    emails = [
        candidate("a", "Stripe", "Backend Engineer", sender="no-reply@greenhouse.io"),
        candidate("b", None, None, sender="no-reply@greenhouse.io"),
    ]
    assert groups(emails, link(emails)) == [["a"], ["b"]]


def test_rule_5_company_only_joins_the_companys_single_application():
    emails = [
        candidate("confirm", "Stripe", "Backend Engineer"),
        candidate("reminder", "Stripe", None),  # names the company only
    ]
    assert groups(emails, link(emails)) == [["confirm", "reminder"]]


def test_rule_5_never_guesses_between_two_applications():
    emails = [
        candidate("a", "Tesla", "Software Engineer"),
        candidate("b", "Tesla", "Data Engineer"),
        candidate("vague", "Tesla", None),
    ]
    assert groups(emails, link(emails)) == [["a"], ["b"], ["vague"]]


def test_rule_5_does_not_move_an_email_already_linked_by_stronger_evidence():
    emails = [
        candidate("a", "Tesla", "Software Engineer"),
        candidate("pierce-1", "Tesla", None, sender="pcorn@tesla.com", thread_id="t9"),
        candidate("pierce-2", "Tesla", None, sender="pcorn@tesla.com", thread_id="t9"),
    ]
    # The Pierce emails are already a group (same thread), so rule 5 leaves them be.
    assert groups(emails, link(emails)) == [["a"], ["pierce-1", "pierce-2"]]


def test_emails_with_no_company_stay_alone():
    emails = [candidate("a", None, None), candidate("b", None, None)]
    assert groups(emails, link(emails)) == [["a"], ["b"]]


def test_links_are_transitive():
    emails = [
        candidate("confirm", "Stripe", "Backend Engineer", requisition_id="R1", thread_id="t1"),
        candidate("reply", None, None, thread_id="t1"),
        candidate("reject", "Stripe", "Backend Engineer", requisition_id="R1"),
    ]
    assert groups(emails, link(emails)) == [["confirm", "reject", "reply"]]


def test_rule_4_same_recruiter_different_requisition_ids_stay_apart():
    emails = [
        candidate("a", "Tesla", None, requisition_id="R1", sender="pcorn@tesla.com"),
        candidate("b", "Tesla", None, requisition_id="R2", sender="pcorn@tesla.com"),
    ]
    assert groups(emails, link(emails)) == [["a"], ["b"]]
