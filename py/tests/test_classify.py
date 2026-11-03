"""Tests for the classifier plumbing. No network: a fake client stands in for the API."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from classify import Classification, cache_key, classify, classify_many, format_email
from data import Email


def make_email(gmail_id="g1", body="Thanks for applying to Stripe.", account="me@gmail.com"):
    return Email(
        account=account,
        gmail_id=gmail_id,
        from_addr="Stripe <no-reply@greenhouse.io>",
        to_addr=account,
        subject="Application received",
        sent_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        body=body,
    )


RESULT = Classification(
    reason="Automated confirmation of an application.",
    category="application_confirmation",
    company="Stripe",
    role=None,
    requisition_id=None,
    deadline=None,
    requested_actions=[],
)


class FakeClient:
    def __init__(self, result=RESULT):
        self.calls = []
        self.result = result
        self.messages = self  # client.messages.parse(...) -> self.parse(...)

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(parsed_output=self.result)


def test_sends_prompt_as_system_and_email_as_user_message(tmp_path):
    client = FakeClient()
    out = classify(make_email(), prompt="PROMPT", model="m", client=client, cache_dir=tmp_path)
    assert out == RESULT
    call = client.calls[0]
    assert call["system"] == "PROMPT"
    assert call["model"] == "m"
    assert call["output_format"] is Classification
    assert "Thanks for applying to Stripe." in call["messages"][0]["content"]


def test_cache_hit_skips_the_api(tmp_path):
    client = FakeClient()
    classify(make_email(), prompt="P", model="m", client=client, cache_dir=tmp_path)
    classify(make_email(), prompt="P", model="m", client=client, cache_dir=tmp_path)
    assert len(client.calls) == 1


def test_changing_the_prompt_or_model_invalidates_the_cache(tmp_path):
    client = FakeClient()
    for prompt, model in [("P1", "m"), ("P2", "m"), ("P1", "m2")]:
        classify(make_email(), prompt=prompt, model=model, client=client, cache_dir=tmp_path)
    assert len(client.calls) == 3


def test_cache_key_separates_fields():
    e1 = make_email(gmail_id="bc")
    e2 = make_email(gmail_id="c")
    assert cache_key("m", "a", e1) != cache_key("m", "ab", e2)


def test_classify_many_preserves_order(tmp_path):
    client = FakeClient()
    emails = [make_email(gmail_id=f"g{i}") for i in range(10)]
    out = classify_many(emails, prompt="P", model="m", client=client, cache_dir=tmp_path)
    assert len(out) == 10
    sent = [c["messages"][0]["content"] for c in client.calls]
    assert len(sent) == 10


def test_email_is_wrapped_and_truncated():
    text = format_email(make_email(body="x" * 20_000))
    assert text.startswith("<email>") and text.endswith("</email>")
    assert "Mailbox owner: me@gmail.com" in text
    assert text.count("x") <= 8_000 + 10


def test_category_case_is_normalized_and_unknown_categories_rejected():
    data = RESULT.model_dump() | {"category": "Rejection"}
    assert Classification.model_validate(data).category == "rejection"
    with pytest.raises(ValidationError):
        Classification.model_validate(RESULT.model_dump() | {"category": "spam"})
