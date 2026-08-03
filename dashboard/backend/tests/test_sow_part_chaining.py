"""Multi-part documents analyze themselves end to end.

The regression: a document that split into more than one part started
NOTHING. The user had to click Analyze once per part, so a large SOW
reliably produced checkpoints and skills for only the parts someone
remembered to click — with no indication the rest existed.

Chaining (each part enqueues the next) rather than fanning out preserves the
existing invariant that only one part of a document is ever in flight.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.visual_qa import ParseStatus
from app.workers.tasks import sow_ingest


class FakeQuery:
    """Just enough of the SQLAlchemy query surface for _chain_next_part."""

    def __init__(self, parts):
        self._parts = parts

    def filter(self, *args, **kw):
        return self

    def order_by(self, *args, **kw):
        return self

    def all(self):
        return sorted(self._parts, key=lambda p: p.part_number)


class FakeSession:
    def __init__(self, parts):
        self._parts = parts
        self.commits = 0

    def query(self, *args, **kw):
        return FakeQuery(self._parts)

    def commit(self):
        self.commits += 1


def _part(n, status=ParseStatus.pending):
    return SimpleNamespace(part_number=n, status=status)


def _artifact(total=5):
    return SimpleNamespace(id="artifact-1", total_parts=total, parse_error=None)


@pytest.fixture
def enqueued(monkeypatch):
    """Capture analyze_sow_part_task.apply_async calls."""
    calls: list[tuple] = []

    monkeypatch.setattr(
        sow_ingest.analyze_sow_part_task,
        "apply_async",
        lambda args, **kw: calls.append((args, kw)),
    )
    return calls


# ── The core behaviour ───────────────────────────────────────────────────────

def test_the_next_pending_part_is_queued(enqueued):
    parts = [_part(1, ParseStatus.done), _part(2), _part(3)]
    sow_ingest._chain_next_part(FakeSession(parts), _artifact(), 1)

    assert len(enqueued) == 1
    args, kw = enqueued[0]
    assert args == ("artifact-1", 2)
    assert kw["countdown"] == sow_ingest._PART_CHAIN_DELAY_S


def test_already_done_parts_are_skipped(enqueued):
    parts = [_part(1, ParseStatus.done), _part(2, ParseStatus.done), _part(3)]
    sow_ingest._chain_next_part(FakeSession(parts), _artifact(), 1)

    assert enqueued[0][0] == ("artifact-1", 3)


def test_nothing_is_queued_when_every_part_is_finished(enqueued):
    parts = [_part(1, ParseStatus.done), _part(2, ParseStatus.done)]
    sow_ingest._chain_next_part(FakeSession(parts), _artifact(), 2)

    assert enqueued == []


def test_a_failed_part_does_not_strand_the_rest(enqueued):
    """One unparseable section must not silently end the document."""
    parts = [_part(1, ParseStatus.done), _part(2, ParseStatus.error), _part(3)]
    sow_ingest._chain_next_part(FakeSession(parts), _artifact(), 2)

    assert enqueued[0][0] == ("artifact-1", 3)


# ── Failure halt ─────────────────────────────────────────────────────────────

def test_a_run_of_failures_halts_the_chain(enqueued):
    """A systematically broken document should cost a few calls to discover,
    not one per part."""
    parts = [
        _part(1, ParseStatus.error),
        _part(2, ParseStatus.error),
        _part(3, ParseStatus.error),
        _part(4),
    ]
    artifact = _artifact()
    sow_ingest._chain_next_part(FakeSession(parts), artifact, 3)

    assert enqueued == []
    assert "consecutive part failures" in artifact.parse_error


def test_an_earlier_isolated_failure_does_not_halt_the_chain(enqueued):
    """Only a CONSECUTIVE run counts — a failure followed by successes is
    not a systemic problem."""
    parts = [
        _part(1, ParseStatus.error),
        _part(2, ParseStatus.done),
        _part(3, ParseStatus.done),
        _part(4),
    ]
    artifact = _artifact()
    sow_ingest._chain_next_part(FakeSession(parts), artifact, 3)

    assert enqueued[0][0] == ("artifact-1", 4)
    assert artifact.parse_error is None


# ── Robustness ───────────────────────────────────────────────────────────────

def test_chaining_is_disabled_by_the_opt_out_flag(enqueued, monkeypatch):
    monkeypatch.setenv("SOW_AUTO_ANALYZE_PARTS", "0")
    parts = [_part(1, ParseStatus.done), _part(2)]
    sow_ingest._chain_next_part(FakeSession(parts), _artifact(), 1)
    assert enqueued == []


def test_auto_analyze_is_on_by_default(monkeypatch):
    monkeypatch.delenv("SOW_AUTO_ANALYZE_PARTS", raising=False)
    assert sow_ingest._auto_analyze_enabled() is True


def test_a_scheduling_failure_never_propagates(monkeypatch):
    """The part that just completed is already committed; a broker hiccup
    must not turn that into an exception."""
    def explode(args, **kw):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(sow_ingest.analyze_sow_part_task, "apply_async", explode)
    parts = [_part(1, ParseStatus.done), _part(2)]

    sow_ingest._chain_next_part(FakeSession(parts), _artifact(), 1)  # must not raise
