"""Measure the classifier against your hand labels.

    uv run python eval_classify.py                  # run, print report, save the run
    uv run python eval_classify.py --model claude-sonnet-5-5

The metric functions marked TODO are yours to implement; `uv run pytest`
tells you when they're right. Everything below them (loading, calling the
classifier, printing, saving runs) is wired up already.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from label import NO_KEY

RUNS_DIR = Path(__file__).resolve().parent.parent / "data" / "runs"


@dataclass(frozen=True)
class Result:
    expected: str  # your label
    predicted: str  # the classifier's category
    bucket: str  # 'keyword' | 'random' | 'targeted'


@dataclass(frozen=True)
class CategoryScore:
    support: int  # how many emails you labeled with this category
    predicted: int  # how many emails the classifier put in this category
    correct: int  # how many it got right in this category

    @property
    def recall(self) -> float | None:
        """Of the emails that ARE this category, the share it found. None if there are none."""
        return self.correct / self.support if self.support else None

    @property
    def precision(self) -> float | None:
        """Of the emails it PUT in this category, the share that belong there. None if it put none."""
        return self.correct / self.predicted if self.predicted else None


# ------------------------------------------------------------------ your part


def accuracy(results: list[Result]) -> float:
    """Share of results where predicted == expected. 0.0 for an empty list."""
    if not results:
        return 0.0
    else:
        return sum(result.predicted == result.expected for result in results) / len(results)


def per_category(results: list[Result]) -> dict[str, CategoryScore]:
    """One CategoryScore per category that appears as expected OR predicted.

    Example: expected=[a, a, b], predicted=[a, b, b]
      a -> support 2, predicted 1, correct 1
      b -> support 1, predicted 2, correct 1
    """
    scores: dict[str, CategoryScore] = {}
    support: Counter[str] = Counter()
    predicted: Counter[str] = Counter()
    correct: Counter[str] = Counter()
    for result in results:
        expected = result.expected
        predicted_ = result.predicted
        support[expected] += 1
        predicted[predicted_] += 1
        if expected == predicted_:
            correct[expected] += 1
    for category in set(support) | set(predicted):
        scores[category] = CategoryScore(
            support=support.get(category, 0),
            predicted=predicted.get(category, 0),
            correct=correct.get(category, 0),
        )
    return scores


def confusions(results: list[Result]) -> list[tuple[str, str, int]]:
    """The mistakes, grouped: (expected, predicted, count) for every pair where they differ.

    Sorted by count, highest first; ties sorted alphabetically by (expected, predicted).
    Correct predictions never appear.
    """
    confusion_counts: Counter[tuple[str, str]] = Counter()
    for result in results:
        expected = result.expected
        predicted = result.predicted
        if expected != predicted:
            confusion_counts[(expected, predicted)] += 1
    return sorted(
        [(expected, predicted, count) for (expected, predicted), count in confusion_counts.items()],
        key=lambda entry: (-entry[2], entry[0], entry[1]),
    )


def recruiting_recall(results: list[Result]) -> tuple[int, int]:
    """(caught, total) over emails you labeled as recruiting.

    "Recruiting" means any category not in NO_KEY (job_alert, not_recruiting).
    "Caught" means the classifier ALSO put it in some recruiting category,
    even the wrong one. This is the number that matters most: a recruiting
    email classified as noise is invisible to the agent, while a wrong
    recruiting category is still seen and can be fixed.
    """
    caught = 0
    total = 0
    for result in results:
        expected = result.expected
        predicted = result.predicted
        if expected not in NO_KEY:
            total += 1
            if predicted not in NO_KEY:
                caught += 1
    return caught, total


def accuracy_by_bucket(results: list[Result]) -> dict[str, float]:
    """Accuracy computed separately for each bucket present in results.

    Targeted emails were hand-picked hard cases, so they're reported apart
    from the sampled ones (keyword + random) instead of blended in.
    """
    buckets = {result.bucket for result in results}
    return {bucket: accuracy([result for result in results if result.bucket == bucket]) for bucket in buckets}


# ------------------------------------------------------------------ wiring


def format_rate(rate: float | None) -> str:
    return "   -" if rate is None else f"{rate:4.0%}"


def print_report(results: list[Result], errors: list[dict[str, str]]) -> dict[str, object]:
    caught, total = recruiting_recall(results)
    overall = accuracy(results)
    by_bucket = accuracy_by_bucket(results)
    summary: dict[str, object] = {
        "n": len(results),
        "accuracy": overall,
        "recruiting_recall": [caught, total],
        "by_bucket": by_bucket,
    }
    print(f"\nAccuracy: {overall:.1%} on {len(results)} emails")
    for bucket, bucket_accuracy in sorted(by_bucket.items()):
        email_count = sum(result.bucket == bucket for result in results)
        print(f"  {bucket:9} {bucket_accuracy:6.1%}  ({email_count} emails)")
    print(f"Recruiting emails caught (any recruiting category): {caught}/{total}")

    print(f"\n{'category':26} {'support':>7} {'recall':>7} {'precision':>9}")
    for category, score in sorted(per_category(results).items()):
        flag = "   <- untested" if score.support == 0 else "   <- few examples" if score.support < 3 else ""
        print(f"{category:26} {score.support:7} {format_rate(score.recall):>7} {format_rate(score.precision):>9}{flag}")

    print("\nMost common mistakes (expected -> predicted):")
    for expected, predicted, mistake_count in confusions(results)[:10]:
        print(f"  {mistake_count:3}  {expected} -> {predicted}")

    print("\nEvery error (read these; they tell you what to change in the prompt):")
    print("  Read one in full with: uv run python label.py show <id>")
    for error in errors:
        print(f"  [{error['bucket']}] {error['subject'][:60]!r}  id={error['gmail_id']}")
        print(f"      expected {error['expected']}, got {error['predicted']}: {error['reason']}")
    return summary


def main() -> None:
    from classify import DEFAULT_MODEL, DEFAULT_PROMPT, classify_many, load_prompt
    from data import load_eval_set

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    prompt = load_prompt(args.prompt)
    items = load_eval_set()
    print(f"Classifying {len(items)} emails with {args.model} (cached results are reused)...")
    outputs = classify_many([item.email for item in items], prompt=prompt, model=args.model, workers=args.workers)

    results = [Result(item.category, output.category, item.bucket) for item, output in zip(items, outputs, strict=True)]
    errors = [
        {
            "subject": item.email.subject,
            "gmail_id": item.email.gmail_id,
            "bucket": item.bucket,
            "expected": item.category,
            "predicted": output.category,
            "reason": output.reason,
        }
        for item, output in zip(items, outputs, strict=True)
        if output.category != item.category
    ]
    summary = print_report(results, errors)

    # Save every run so prompt versions can be compared later.
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = {
        "at": stamp,
        "model": args.model,
        "prompt_file": str(args.prompt.name),
        "prompt_sha": hashlib.sha256(prompt.encode()).hexdigest()[:12],
        "summary": summary,
        "results": [asdict(result) for result in results],
        "errors": errors,
    }
    out = RUNS_DIR / f"classify-{stamp}.json"
    out.write_text(json.dumps(run, indent=2), encoding="utf-8")
    print(f"\nSaved run to {out}")


if __name__ == "__main__":
    main()
