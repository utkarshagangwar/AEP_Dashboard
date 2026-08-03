"""Truncation detection in llm_router (the silent-content-loss root cause).

The bug these guard against: a structured-JSON response cut off by
max_tokens does not fail. Its JSON doesn't parse, so complete() runs a
repair pass — and the repair prompt is much SHORTER than the original, so it
finishes cleanly and returns a valid, quietly shortened array. Reporting the
repair call's finish_reason therefore reports "stop" for a call that lost
content, which is why checking finish_reason alone never caught this.

No network: litellm.completion is monkeypatched.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import llm_router


def _response(content: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        usage=None,
    )


@pytest.fixture
def fake_litellm(monkeypatch):
    """Patch litellm.completion with a scripted response queue."""
    import litellm

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("VISUAL_LLM_PRIMARY", "gemini/test-model")
    monkeypatch.setenv("VISUAL_LLM_FALLBACKS", "")
    monkeypatch.setattr(llm_router, "_log_usage", lambda **kw: None)

    state = {"queue": [], "calls": []}

    def fake_completion(*, model, messages, max_tokens, **kw):
        state["calls"].append({"model": model, "max_tokens": max_tokens})
        return state["queue"].pop(0)

    monkeypatch.setattr(litellm, "completion", fake_completion)
    return state


# ── The core regression ──────────────────────────────────────────────────────

def test_truncated_original_is_reported_even_when_the_repair_finishes_cleanly(
    fake_litellm,
):
    """THE test. Original cut off mid-array -> unparseable -> repair returns
    valid JSON with finish_reason='stop'. finish_reason must still report the
    repair's value (unchanged contract, visual_judge depends on it) while
    `truncated` remembers that content was lost."""
    fake_litellm["queue"] = [
        _response('{"facts": [{"label": "a"}, {"label": "b"}, {"lab', "length"),
        _response('{"facts": [{"label": "a"}, {"label": "b"}]}', "stop"),
    ]

    result = llm_router.complete("p", expect_json=True, max_tokens=100)

    assert result.truncated is True, "the original response's cutoff was lost"
    assert result.repaired is True
    assert result.finish_reason == "stop"  # unchanged: the repair produced `text`
    assert result.parsed_json == {"facts": [{"label": "a"}, {"label": "b"}]}


def test_clean_response_is_not_flagged(fake_litellm):
    fake_litellm["queue"] = [_response('{"facts": []}', "stop")]
    result = llm_router.complete("p", expect_json=True, max_tokens=100)
    assert result.truncated is False
    assert result.repaired is False


def test_truncated_but_still_parseable_is_flagged(fake_litellm):
    """A response can be cut off at a point where the JSON happens to close
    (or the outermost-braces fallback salvages it) — no repair runs, but
    content is still missing."""
    fake_litellm["queue"] = [_response('{"facts": [{"label": "a"}]}', "length")]
    result = llm_router.complete("p", expect_json=True, max_tokens=100)
    assert result.truncated is True
    assert result.repaired is False


# ── Escalation helper ────────────────────────────────────────────────────────

def test_complete_json_complete_retries_once_at_double_the_budget(fake_litellm):
    fake_litellm["queue"] = [
        _response('{"facts": [{"label": "a"}]}', "length"),
        _response('{"facts": [{"label": "a"}, {"label": "b"}]}', "stop"),
    ]

    result = llm_router.complete_json_complete("p", max_tokens=1000)

    assert [c["max_tokens"] for c in fake_litellm["calls"]] == [1000, 2000]
    assert result.truncated is False
    assert len(result.parsed_json["facts"]) == 2


def test_complete_json_complete_does_not_retry_a_clean_call(fake_litellm):
    fake_litellm["queue"] = [_response('{"facts": []}', "stop")]
    llm_router.complete_json_complete("p", max_tokens=1000)
    assert len(fake_litellm["calls"]) == 1


def test_complete_json_complete_surfaces_persistent_truncation(fake_litellm):
    """When even the escalated call is truncated, the flag must survive so
    the caller can take a structural remedy (splitting its input) instead of
    assuming the short answer was complete."""
    fake_litellm["queue"] = [
        _response('{"facts": [{"label": "a"}]}', "length"),
        _response('{"facts": [{"label": "a"}]}', "length"),
    ]

    result = llm_router.complete_json_complete("p", max_tokens=1000, escalations=1)

    assert len(fake_litellm["calls"]) == 2
    assert result.truncated is True
    assert result.parsed_json is not None, "the partial result is still returned"


def test_escalation_never_exceeds_the_ceiling(fake_litellm):
    fake_litellm["queue"] = [
        _response('{"facts": []}', "length"),
        _response('{"facts": []}', "length"),
    ]
    llm_router.complete_json_complete(
        "p", max_tokens=llm_router._MAX_TOKENS_CEILING, escalations=3
    )
    # Already at the ceiling — nothing to escalate to, so no retry at all.
    assert len(fake_litellm["calls"]) == 1
