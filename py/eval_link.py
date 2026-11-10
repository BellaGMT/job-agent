"""Measure linking against your application keys.

    uv run python eval_link.py                      # link the labeled recruiting emails, print the report
    uv run python eval_link.py --from-position 139  # only a batch queued later (held out)

Linking is evaluated on its own: it runs on the emails YOU labeled as
recruiting, so classifier mistakes don't show up as linking mistakes. Company,
role and requisition id come from the classifier (cached, so usually free).

Scores are pairwise. For every pair of emails, the question is "same
application?". Precision: of the pairs linked, the share that truly belong
together (low = wrong merges). Recall: of the pairs that belong together, the
share linked (low = applications split into pieces). Large applications
contribute many pairs, so read the split and merge lists, not only the numbers.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from datetime import datetime
from math import comb
from pathlib import Path
from typing import TypeVar

from eval_classify import RUNS_DIR, format_rate
from link import Candidate, link

Outer = TypeVar("Outer", bound=Hashable)


@dataclass(frozen=True)
class PairScores:
    true_pairs: int  # pairs that share an application key
    linked_pairs: int  # pairs the linker put in one group
    correct_pairs: int  # pairs that are both

    @property
    def precision(self) -> float | None:
        return self.correct_pairs / self.linked_pairs if self.linked_pairs else None

    @property
    def recall(self) -> float | None:
        return self.correct_pairs / self.true_pairs if self.true_pairs else None


def pairwise_scores(expected_keys: Sequence[str], predicted_ids: Sequence[int]) -> PairScores:
    """Counted from group sizes rather than by visiting every pair: a group of n holds n*(n-1)/2 pairs."""
    return PairScores(
        true_pairs=_pairs_within(expected_keys),
        linked_pairs=_pairs_within(predicted_ids),
        correct_pairs=_pairs_within(list(zip(expected_keys, predicted_ids, strict=True))),
    )


def _pairs_within(labels: Sequence[Hashable]) -> int:
    return sum(comb(size, 2) for size in Counter(labels).values())


def splits(expected_keys: Sequence[str], predicted_ids: Sequence[int]) -> dict[str, list[list[int]]]:
    """Applications spread over several groups: key -> its emails' positions, one list per group."""
    return _spread(expected_keys, predicted_ids)


def merges(expected_keys: Sequence[str], predicted_ids: Sequence[int]) -> dict[int, list[list[int]]]:
    """Groups mixing several applications: group id -> its emails' positions, one list per key."""
    return _spread(predicted_ids, expected_keys)


def _spread(outer: Sequence[Outer], inner: Sequence[Hashable]) -> dict[Outer, list[list[int]]]:
    """Each outer label that covers more than one inner label, with its positions grouped by inner label."""
    nested: defaultdict[Outer, defaultdict[Hashable, list[int]]] = defaultdict(lambda: defaultdict(list))
    for position, (outer_label, inner_label) in enumerate(zip(outer, inner, strict=True)):
        nested[outer_label][inner_label].append(position)
    return {label: list(parts.values()) for label, parts in nested.items() if len(parts) > 1}


# ------------------------------------------------------------------ report


def describe(candidate: Candidate) -> str:
    extracted = " | ".join(field or "?" for field in (candidate.company, candidate.role, candidate.requisition_id))
    return f"{candidate.email.subject[:50]!r}  id={candidate.email.gmail_id}  extracted: {extracted}"


def print_report(candidates: Sequence[Candidate], expected_keys: Sequence[str], ids: Sequence[int]) -> PairScores:
    scores = pairwise_scores(expected_keys, ids)
    print(f"\nLinked {len(candidates)} recruiting emails")
    print(f"  applications in your labels: {len(set(expected_keys))}   groups found: {len(set(ids))}")
    print(f"  pairwise precision {format_rate(scores.precision):>5}   (low = different applications merged)")
    print(f"  pairwise recall    {format_rate(scores.recall):>5}   (low = one application split up)")

    print("\nSplit applications (one application, several groups):")
    print("  Read one in full with: uv run python label.py show <id>")
    for key, parts in splits(expected_keys, ids).items():
        print(f"  {key}")
        for number, part in enumerate(parts, 1):
            for position in part:
                print(f"    group {number}: {describe(candidates[position])}")

    print("\nMerged applications (one group, several applications):")
    for parts in merges(expected_keys, ids).values():
        print("  one group:")
        for part in parts:
            for position in part:
                print(f"    {expected_keys[position]}: {describe(candidates[position])}")
    return scores


def main() -> None:
    from classify import DEFAULT_MODEL, DEFAULT_PROMPT, classify_many, load_prompt
    from data import load_eval_set

    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--from-position", type=int, default=0)
    args = parser.parse_args()

    items = [
        item for item in load_eval_set(from_position=args.from_position) if item.is_recruiting and item.application_key
    ]
    if not items:
        raise SystemExit("No labeled recruiting emails with an application key to link.")
    print(f"Extracting fields from {len(items)} emails with {args.model} (cached results are reused)...")
    outputs = classify_many([item.email for item in items], prompt=load_prompt(args.prompt), model=args.model)
    candidates = [
        Candidate(item.email, output.company, output.role, output.requisition_id)
        for item, output in zip(items, outputs, strict=True)
    ]
    expected_keys = [item.application_key or "" for item in items]
    ids = link(candidates)
    scores = print_report(candidates, expected_keys, ids)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run = {
        "at": stamp,
        "model": args.model,
        "from_position": args.from_position,
        "pairs": {"true": scores.true_pairs, "linked": scores.linked_pairs, "correct": scores.correct_pairs},
        "precision": scores.precision,
        "recall": scores.recall,
        "emails": [
            {"gmail_id": item.email.gmail_id, "expected": key, "group": group}
            for item, key, group in zip(items, expected_keys, ids, strict=True)
        ],
    }
    out = RUNS_DIR / f"link-{stamp}.json"
    out.write_text(json.dumps(run, indent=2), encoding="utf-8")
    print(f"\nSaved run to {out}")


if __name__ == "__main__":
    main()
