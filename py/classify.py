"""Classify one email and extract its fields with a single structured model call.

This is a pipeline step, not an agent: one email in, one validated object out,
no tools and no loop. The schema below is enforced by the API (structured
outputs), so the result always parses; whether it's *right* is what the eval
measures.

Results are cached on disk, keyed by the model, the prompt and the email text
exactly as the model sees it, earlier thread messages included. Re-running the eval after changing nothing costs
nothing. Editing the prompt, re-syncing an email with a cleaner body, or
changing how emails are formatted changes the key, so those are re-classified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, field_validator

from data import Email

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # ANTHROPIC_API_KEY lives in the repo-root .env

DEFAULT_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-haiku-5-5")
DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "classify.md"
CACHE_DIR = ROOT / "data" / "cache" / "classify"
MAX_BODY_CHARS = 8_000  # long enough for any recruiting email; caps cost on newsletters
# Thread context: the latest few earlier messages, each cut short. A reply's own
# text comes first and quoted history after, so the start of each body is the part
# that matters; older history is usually quoted in the email being classified anyway.
MAX_THREAD_MESSAGES = 5
MAX_THREAD_BODY_CHARS = 1_500
REQUEST_TIMEOUT_S = 60.0
_DATA_TAG = re.compile(r"<\s*(/?)\s*(email|thread)\b", re.IGNORECASE)
MAX_RETRIES = 4  # the SDK retries 429/5xx/timeouts with exponential backoff and honors Retry-After

Category = Literal[
    "application_confirmation",
    "recruiter_outreach",
    "assessment_invite",
    "interview_invite",
    "interview_scheduling",
    "info_request",
    "rejection",
    "offer",
    "recruiting_update",
    "job_alert",
    "not_recruiting",
]


class Classification(BaseModel):
    # Field order matters: the model writes fields in this order, so asking for
    # the reason first lets it reason before it commits to a category.
    reason: str = Field(description="One sentence: what this email is and why it gets this category.")
    category: Category
    company: str | None = Field(description="Hiring company, not the ATS or job board. null if not stated.")
    role: str | None = Field(description="Job title as written. null if not stated.")
    requisition_id: str | None = Field(description="Job/requisition id if present, else null.")
    deadline: str | None = Field(description="ISO date (YYYY-MM-DD) of any deadline in the email, else null.")
    requested_actions: list[str] = Field(description="Things the recipient is asked to do. Empty if none.")

    @field_validator("category", mode="before")
    @classmethod
    def _lowercase(cls, value: object) -> object:
        # The API may change the capitalization of enum values ("Rejection");
        # normalize before validating against the Literal.
        return value.strip().lower() if isinstance(value, str) else value


class ClassificationError(RuntimeError):
    """The API answered but gave no usable classification (refusal, truncation)."""


# `client.messages.parse` from the Anthropic SDK, or a fake in tests.
ParseFn = Callable[..., object]


def default_parse() -> ParseFn:
    from anthropic import Anthropic

    # Reads ANTHROPIC_API_KEY. One client is safe to share across threads.
    return Anthropic(timeout=REQUEST_TIMEOUT_S, max_retries=MAX_RETRIES).messages.parse


def format_email(email: Email) -> str:
    """The user message: earlier thread messages if any, then the email to classify, inside tags.

    The tags mark where untrusted content starts and ends. Email text is data,
    never instructions; the system prompt says so. Any `<email>` or `<thread>`
    tag inside the content is defanged so a message can't close its block
    early. This narrows prompt injection; it doesn't eliminate it, which is why
    the classifier has no tools and its output is schema-constrained.
    """
    message = (
        f"<email>\nMailbox owner: {email.account}\n{_headers(email)}\n{_defang(email.body[:MAX_BODY_CHARS])}\n</email>"
    )
    earlier = email.thread[-MAX_THREAD_MESSAGES:]
    if not earlier:
        return message
    context = "\n\n---\n\n".join(
        f"{_headers(previous)}\n{_defang(previous.body[:MAX_THREAD_BODY_CHARS])}" for previous in earlier
    )
    omitted = len(email.thread) - len(earlier)
    note = f" ({omitted} older not shown)" if omitted else ""
    return f"<thread>\nEarlier messages in this conversation, oldest first{note}.\n\n{context}\n</thread>\n\n{message}"


def _headers(email: Email) -> str:
    return (
        f"From: {_defang(email.from_addr)}\n"
        f"To: {_defang(email.to_addr)}\n"
        f"Date: {email.sent_at:%Y-%m-%d}\n"
        f"Subject: {_defang(email.subject)}\n"
    )


def _defang(text: str) -> str:
    """'</email>' -> '[/email>', '<thread>' -> '[thread>' (case and spacing variants too), text stays readable."""
    return _DATA_TAG.sub(r"[\1\2", text)


def cache_key(model: str, prompt: str, email: Email) -> str:
    digest = hashlib.sha256()
    # Hash what the model actually receives: content, not ids, so a stored email
    # whose text changes can never be served an answer computed from the old text.
    for part in (model, prompt, format_email(email)):
        digest.update(part.encode())
        digest.update(b"\0")  # separator, so ("ab","c") and ("a","bc") differ
    return digest.hexdigest()[:32]


def classify(
    email: Email,
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    parse: ParseFn | None = None,
    cache_dir: Path | None = CACHE_DIR,
) -> Classification:
    path = cache_dir / f"{cache_key(model, prompt, email)}.json" if cache_dir else None
    cached = _read_cache(path) if path else None
    if cached:
        return cached

    response = (parse or default_parse())(
        model=model,
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": format_email(email)}],
        output_format=Classification,
    )
    result = _validated(response)
    if path:
        _write_cache(path, result)
    return result


def classify_many(
    emails: list[Email],
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    workers: int = 4,
    parse: ParseFn | None = None,
    cache_dir: Path | None = CACHE_DIR,
) -> list[Classification]:
    """Classify in parallel; results come back in the same order as `emails`.

    If one call fails, the error propagates, but every result already written
    to the cache is kept, so a re-run resumes instead of starting over.
    """
    shared = parse or default_parse()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(
                lambda email: classify(email, prompt=prompt, model=model, parse=shared, cache_dir=cache_dir), emails
            )
        )


def _validated(response: object) -> Classification:
    """Don't trust the response shape: a refusal or a max_tokens cut-off yields no parsed output."""
    parsed = getattr(response, "parsed_output", None)
    if isinstance(parsed, Classification):
        return parsed
    stop = getattr(response, "stop_reason", "unknown")
    raise ClassificationError(f"No valid classification in the response (stop_reason={stop}).")


def _read_cache(path: Path) -> Classification | None:
    """A missing or unreadable cache file is a cache miss, never a crash."""
    try:
        return Classification.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValidationError, ValueError):
        return None  # corrupt or from an older schema: re-classify and overwrite


def _write_cache(path: Path, result: Classification) -> None:
    """Write to a temp file and rename, so a crash mid-write can't leave a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_prompt(path: Path = DEFAULT_PROMPT) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text or text.startswith("TODO"):
        raise SystemExit(f"Write your classifier prompt in {path} first (see the checklist in that file).")
    return text


if __name__ == "__main__":
    # Quick manual check: classify the 3 most recent labeled emails.
    from data import load_eval_set

    items = load_eval_set()[-3:]
    outputs = classify_many([item.email for item in items], prompt=load_prompt())
    for item, classification in zip(items, outputs, strict=True):
        print(f"{item.email.subject[:60]!r}\n  expected {item.category}, got {classification.category}")
        print(f"  {json.dumps(classification.model_dump())}\n")
