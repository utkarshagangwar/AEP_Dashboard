"""TDD_DERIVED_AS_SKILLS must apply to BOTH ingest workers.

The flag is documented as keeping derived negative/edge checkpoints out of the
Skills table. It describes the TABLE, not one source feeding it — so a flag
that applied to documents but not to walkthrough videos would be worse than no
flag at all: the table would look filtered while half of it was not, and
nothing in the UI would say which half.

These tests run the two workers' skill-capture functions against a fake
session, so no database is involved.
"""
from __future__ import annotations

import contextlib

import pytest

from app.workers.tasks import sow_ingest, video_ingest


class _FakeSession:
    """Enough of a SQLAlchemy Session for _save_functional_skills."""

    def begin_nested(self):
        return contextlib.nullcontext()

    def flush(self):
        pass


class _FakeArtifact:
    id = "artifact-1"
    project_id = None


@pytest.fixture
def captured(monkeypatch):
    """Record what each worker tried to save instead of hitting the DB."""
    calls: list[dict] = []

    def _fake_upsert(db, **kwargs):
        calls.append(kwargs)
        return object()

    from app.services import skill_store

    monkeypatch.setattr(skill_store, "upsert_prompt_skill", _fake_upsert)
    return calls


def _checkpoints():
    return [
        {
            "type": "functional",
            "title": "Create a job",
            "description": "# Objective\nCreate a job",
            "test_type": "positive",
            "grounding": "stated",
            "behaviour_key": "create-a-job",
        },
        {
            "type": "functional",
            "title": "Reject an empty title",
            "description": "# Objective\nReject an empty title",
            "test_type": "negative",
            "grounding": "derived",
            "behaviour_key": "create-a-job",
        },
    ]


def _saved_titles(calls):
    return [c["title"] for c in calls]


# ── Video worker ─────────────────────────────────────────────────────────────

def test_video_worker_saves_derived_by_default(monkeypatch, captured):
    """Default is ON: a suite with no negative coverage is the problem this
    pipeline exists to fix, so derived cases become runnable skills."""
    monkeypatch.delenv("TDD_DERIVED_AS_SKILLS", raising=False)
    video_ingest._save_functional_skills(_FakeSession(), _FakeArtifact(), _checkpoints())
    assert _saved_titles(captured) == ["Create a job", "Reject an empty title"]


def test_video_worker_holds_derived_when_the_flag_is_off(monkeypatch, captured):
    monkeypatch.setenv("TDD_DERIVED_AS_SKILLS", "0")
    video_ingest._save_functional_skills(_FakeSession(), _FakeArtifact(), _checkpoints())
    assert _saved_titles(captured) == ["Create a job"]


# ── SOW worker (parity — this one already honoured the flag) ─────────────────

def test_sow_worker_saves_derived_by_default(monkeypatch, captured):
    monkeypatch.delenv("TDD_DERIVED_AS_SKILLS", raising=False)
    sow_ingest._save_functional_skills(_FakeSession(), _FakeArtifact(), _checkpoints(), 1)
    assert _saved_titles(captured) == ["Create a job", "Reject an empty title"]


def test_sow_worker_holds_derived_when_the_flag_is_off(monkeypatch, captured):
    monkeypatch.setenv("TDD_DERIVED_AS_SKILLS", "0")
    sow_ingest._save_functional_skills(_FakeSession(), _FakeArtifact(), _checkpoints(), 1)
    assert _saved_titles(captured) == ["Create a job"]


def test_both_workers_agree_with_the_flag_off(monkeypatch, captured):
    """The property that matters: one flag, one outcome, regardless of which
    source produced the checkpoint."""
    monkeypatch.setenv("TDD_DERIVED_AS_SKILLS", "0")
    video_ingest._save_functional_skills(_FakeSession(), _FakeArtifact(), _checkpoints())
    from_video = _saved_titles(captured)
    captured.clear()
    sow_ingest._save_functional_skills(_FakeSession(), _FakeArtifact(), _checkpoints(), 1)
    assert _saved_titles(captured) == from_video
