"""Specs for the eval metrics. These fail until you implement the TODOs in eval_classify.py.

Run: uv run pytest tests/test_eval_classify.py -v
"""

import pytest

from eval_classify import (
    CategoryScore,
    Result,
    accuracy,
    accuracy_by_bucket,
    confusions,
    per_category,
    recruiting_recall,
)


def R(expected, predicted, bucket="keyword"):
    return Result(expected, predicted, bucket)


# --- accuracy ---------------------------------------------------------------


def test_accuracy_counts_exact_matches():
    results = [R("rejection", "rejection"), R("offer", "rejection"), R("job_alert", "job_alert"), R("offer", "offer")]
    assert accuracy(results) == pytest.approx(0.75)


def test_accuracy_of_nothing_is_zero():
    assert accuracy([]) == 0.0


# --- per_category -----------------------------------------------------------


def test_per_category_counts_support_predicted_and_correct():
    results = [R("a", "a"), R("a", "b"), R("b", "b")]
    scores = per_category(results)
    assert scores["a"] == CategoryScore(support=2, predicted=1, correct=1)
    assert scores["b"] == CategoryScore(support=1, predicted=2, correct=1)
    assert scores["a"].recall == pytest.approx(0.5)
    assert scores["b"].precision == pytest.approx(0.5)


def test_per_category_includes_categories_only_ever_predicted():
    # The classifier invented an "offer" that you never labeled: precision must show that.
    scores = per_category([R("rejection", "offer")])
    assert scores["offer"] == CategoryScore(support=0, predicted=1, correct=0)
    assert scores["offer"].recall is None
    assert scores["offer"].precision == 0.0
    assert scores["rejection"].precision is None


def test_per_category_has_no_extra_categories():
    assert set(per_category([R("a", "a")])) == {"a"}


# --- confusions -------------------------------------------------------------


def test_confusions_counts_only_mistakes_most_common_first():
    results = [
        R("interview_invite", "interview_scheduling"),
        R("interview_invite", "interview_scheduling"),
        R("recruiter_outreach", "job_alert"),
        R("rejection", "rejection"),
    ]
    assert confusions(results) == [
        ("interview_invite", "interview_scheduling", 2),
        ("recruiter_outreach", "job_alert", 1),
    ]


def test_confusions_breaks_ties_alphabetically():
    results = [R("b", "a"), R("a", "c"), R("a", "b")]
    assert confusions(results) == [("a", "b", 1), ("a", "c", 1), ("b", "a", 1)]


def test_no_mistakes_no_confusions():
    assert confusions([R("a", "a")]) == []


# --- recruiting_recall ------------------------------------------------------


def test_wrong_recruiting_category_still_counts_as_caught():
    results = [
        R("interview_invite", "interview_scheduling"),  # wrong category, still caught
        R("offer", "offer"),  # caught
        R("recruiter_outreach", "job_alert"),  # missed: became noise
        R("rejection", "not_recruiting"),  # missed
    ]
    assert recruiting_recall(results) == (2, 4)


def test_non_recruiting_emails_are_ignored():
    results = [R("job_alert", "rejection"), R("not_recruiting", "not_recruiting"), R("offer", "offer")]
    assert recruiting_recall(results) == (1, 1)


def test_recruiting_recall_with_no_recruiting_emails():
    assert recruiting_recall([R("job_alert", "job_alert")]) == (0, 0)


# --- accuracy_by_bucket -----------------------------------------------------


def test_accuracy_by_bucket_scores_each_bucket_separately():
    results = [
        R("a", "a", "keyword"),
        R("a", "b", "keyword"),
        R("a", "a", "random"),
        R("a", "b", "targeted"),
    ]
    assert accuracy_by_bucket(results) == {"keyword": 0.5, "random": 1.0, "targeted": 0.0}


def test_accuracy_by_bucket_only_lists_buckets_present():
    assert set(accuracy_by_bucket([R("a", "a", "random")])) == {"random"}
