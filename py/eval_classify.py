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
    raise NotImplementedError


def per_category(results: list[Result]) -> dict[str, CategoryScore]:
    """One CategoryScore per category that appears as expected OR predicted.

    Example: expected=[a, a, b], predicted=[a, b, b]
      a -> support 2, predicted 1, correct 1
      b -> support 1, predicted 2, correct 1
    """
    raise NotImplementedError


def confusions(results: list[Result]) -> list[tuple[str, str, int]]:
    """The mistakes, grouped: (expected, predicted, count) for every pair where they differ.

    Sorted by count, highest first; ties sorted alphabetically by (expected, predicted).
    Correct predictions never appear.
    """
    raise NotImplementedError


def recruiting_recall(results: list[Result]) -> tuple[int, int]:
    """(caught, total) over emails you labeled as recruiting.

    "Recruiting" means any category not in NO_KEY (job_alert, not_recruiting).
    "Caught" means the classifier ALSO put it in some recruiting category,
    even the wrong one. This is the number that matters most: a recruiting
    email classified as noise is invisible to the agent, while a wrong
    recruiting category is still seen and can be fixed.
    """
    raise NotImplementedError


def accuracy_by_bucket(results: list[Result]) -> dict[str, float]:
    """Accuracy computed separately for each bucket present in results.

    Targeted emails were hand-picked hard cases, so they're reported apart
    from the sampled ones (keyword + random) instead of blended in.
    """
    raise NotImplementedError


# ------------------------------------------------------------------ wiring


def fmt(x: float | None) -> str:
    return "   -" if x is None else f"{x:4.0%}"


def print_report(results: list[Result], errors: list[dict]) -> dict:
    caught, total = recruiting_recall(results)
    summary = {
        "n": len(results),
        "accuracy": accuracy(results),
        "recruiting_recall": [caught, total],
        "by_bucket": accuracy_by_bucket(results),
    }
    print(f"\nAccuracy: {summary['accuracy']:.1%} on {len(results)} emails")
    for bucket, acc in sorted(summary["by_bucket"].items()):
        n = sum(r.bucket == bucket for r in results)
        print(f"  {bucket:9} {acc:6.1%}  ({n} emails)")
    print(f"Recruiting emails caught (any recruiting category): {caught}/{total}")

    print(f"\n{'category':26} {'support':>7} {'recall':>7} {'precision':>9}")
    for cat, s in sorted(per_category(results).items()):
        flag = "   <- untested" if s.support == 0 else "   <- few examples" if s.support < 3 else ""
        print(f"{cat:26} {s.support:7} {fmt(s.recall):>7} {fmt(s.precision):>9}{flag}")

    print("\nMost common mistakes (expected -> predicted):")
    for exp, pred, n in confusions(results)[:10]:
        print(f"  {n:3}  {exp} -> {pred}")

    print("\nEvery error (read these; they tell you what to change in the prompt):")
    for e in errors:
        print(f"  [{e['bucket']}] {e['subject'][:60]!r}")
        print(f"      expected {e['expected']}, got {e['predicted']}: {e['reason']}")
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
    outputs = classify_many([i.email for i in items], prompt=prompt, model=args.model, workers=args.workers)

    results = [Result(i.category, o.category, i.bucket) for i, o in zip(items, outputs)]
    errors = [
        {
            "subject": i.email.subject,
            "gmail_id": i.email.gmail_id,
            "bucket": i.bucket,
            "expected": i.category,
            "predicted": o.category,
            "reason": o.reason,
        }
        for i, o in zip(items, outputs)
        if o.category != i.category
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
        "results": [asdict(r) for r in results],
        "errors": errors,
    }
    out = RUNS_DIR / f"classify-{stamp}.json"
    out.write_text(json.dumps(run, indent=2), encoding="utf-8")
    print(f"\nSaved run to {out}")


if __name__ == "__main__":
    main()
