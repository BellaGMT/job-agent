"""Classify one email and extract its fields with a single structured model call.

This is a pipeline step, not an agent: one email in, one validated object out,
no tools and no loop. The schema below is enforced by the API (structured
outputs), so the result always parses; whether it's *right* is what the eval
measures.

Results are cached on disk, keyed by (model, prompt, email). Re-running the
eval after changing nothing costs nothing; editing the prompt changes the key,
so every email is re-classified with the new prompt.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Protocol

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

from data import Email

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # ANTHROPIC_API_KEY lives in the repo-root .env

DEFAULT_MODEL = os.environ.get("CLASSIFY_MODEL", "claude-haiku-5-5")
DEFAULT_PROMPT = Path(__file__).resolve().parent / "prompts" / "classify.md"
CACHE_DIR = ROOT / "data" / "cache" / "classify"
MAX_BODY_CHARS = 8_000  # long enough for any recruiting email; caps cost on newsletters

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
    def _lowercase(cls, v: object) -> object:
        # The API may change the capitalization of enum values ("Rejection");
        # normalize before validating against the Literal.
        return v.strip().lower() if isinstance(v, str) else v


class MessagesClient(Protocol):
    """The slice of the Anthropic client we use; tests pass a fake."""

    @property
    def messages(self): ...


def format_email(email: Email) -> str:
    """The user message: metadata the model needs, then the body, inside tags.

    The tags mark where untrusted content starts and ends. Email text is data,
    never instructions; the system prompt should say so.
    """
    body = email.body[:MAX_BODY_CHARS]
    return (
        f"<email>\n"
        f"Mailbox owner: {email.account}\n"
        f"From: {email.from_addr}\n"
        f"To: {email.to_addr}\n"
        f"Date: {email.sent_at:%Y-%m-%d}\n"
        f"Subject: {email.subject}\n\n"
        f"{body}\n"
        f"</email>"
    )


def cache_key(model: str, prompt: str, email: Email) -> str:
    h = hashlib.sha256()
    for part in (model, prompt, email.account, email.gmail_id, str(MAX_BODY_CHARS)):
        h.update(part.encode())
        h.update(b"\0")  # separator, so ("ab","c") and ("a","bc") differ
    return h.hexdigest()[:32]


def classify(
    email: Email,
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    client: MessagesClient | None = None,
    cache_dir: Path | None = CACHE_DIR,
) -> Classification:
    path = cache_dir / f"{cache_key(model, prompt, email)}.json" if cache_dir else None
    if path and path.exists():
        return Classification.model_validate_json(path.read_text(encoding="utf-8"))

    if client is None:
        from anthropic import Anthropic

        client = Anthropic()  # reads ANTHROPIC_API_KEY; retries 429/5xx itself
    response = client.messages.parse(
        model=model,
        max_tokens=1024,
        system=prompt,
        messages=[{"role": "user", "content": format_email(email)}],
        output_format=Classification,
    )
    result: Classification = response.parsed_output

    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def classify_many(
    emails: list[Email],
    *,
    prompt: str,
    model: str = DEFAULT_MODEL,
    workers: int = 4,
    client: MessagesClient | None = None,
    cache_dir: Path | None = CACHE_DIR,
) -> list[Classification]:
    """Classify in parallel; results come back in the same order as `emails`."""
    if client is None:
        from anthropic import Anthropic

        client = Anthropic()  # one client shared by all threads (it's thread-safe)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(
            pool.map(
                lambda e: classify(e, prompt=prompt, model=model, client=client, cache_dir=cache_dir),
                emails,
            )
        )


def load_prompt(path: Path = DEFAULT_PROMPT) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text or text.startswith("TODO"):
        raise SystemExit(f"Write your classifier prompt in {path} first (see the checklist in that file).")
    return text


if __name__ == "__main__":
    # Quick manual check: classify the 3 most recent labeled emails.
    from data import load_eval_set

    items = load_eval_set()[-3:]
    for item, c in zip(items, classify_many([i.email for i in items], prompt=load_prompt())):
        print(f"{item.email.subject[:60]!r}\n  expected {item.category}, got {c.category}\n  {json.dumps(c.model_dump())}\n")
