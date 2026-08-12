"""Unit tests for app.services.flow_validation.

Scope: the whole module, because all of it is deterministic — graph building,
reachability, path resolution, cumulative locks, the annotate-never-drop
contract, and the prompt fragment. No network, no database, no LLM.

The behaviour these tests pin down is the answer to "the extractor produces
test cases that cannot be run": a checkpoint is only runnable if a tester can
reach the state it starts from, and a checkpoint that cannot is FLAGGED, not
deleted. Concretely:

  * no flow model changes nothing at all      — test_no_flow_model_*
  * an anchored checkpoint gains its full setup path
                                              — test_validate_anchors_*
  * an unreachable one is flagged and kept    — test_validate_flags_*
  * an existing review_status is never clobbered
                                              — test_existing_review_status_wins
  * a locked behaviour is flagged only when claimed to SUCCEED
                                              — test_locked_*
  * every malformed-model path fails open     — test_*_fails_open, test_cycle_*
"""
from __future__ import annotations

import json

import pytest

from app.services import flow_validation as fv


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def flow() -> dict:
    """A four-state flow with one lock, mirroring a real create-then-lock app."""
    return {
        "states": [
            {"id": "S0", "requires": [], "pages": ["login"]},
            {"id": "S1", "requires": ["S0"]},
            {"id": "S2", "requires": ["S1"], "pages": ["create job"],
             "locked_behaviours": []},
            {"id": "S3", "requires": ["S2"],
             "locked_behaviours": ["edit job description"]},
        ]
    }


def _cp(**kw) -> dict:
    """A checkpoint with only the keys flow_validation reads."""
    base = {"title": "t", "objective": None, "expected": None, "instructions": []}
    base.update(kw)
    return base


# ── No flow model: the feature must be completely inert ─────────────────────

def test_no_flow_model_returns_empty_summary():
    assert fv.validate([_cp()], None) == {}


def test_no_flow_model_leaves_checkpoints_untouched():
    cps = [_cp(title="a"), _cp(title="b")]
    snapshot = json.dumps(cps, sort_keys=True)
    fv.validate(cps, None)
    assert json.dumps(cps, sort_keys=True) == snapshot


def test_no_checkpoints_is_a_no_op(flow):
    assert fv.validate([], flow) == {}


def test_disabled_by_flag_is_a_no_op(flow, monkeypatch):
    monkeypatch.setenv("TDD_FLOW_VALIDATION", "0")
    cps = [_cp(precondition_state="S2")]
    assert fv.validate(cps, flow) == {}
    assert "setup_path" not in cps[0]


# ── Graph ────────────────────────────────────────────────────────────────────

def test_entry_state_is_the_single_root(flow):
    _, entry = fv.build_index(flow)
    assert entry == "S0"


def test_two_roots_means_no_entry_state():
    """Ambiguous start = unusable model. Picking one would silently change
    which checkpoints validate."""
    _, entry = fv.build_index({"states": [{"id": "A"}, {"id": "B"}]})
    assert entry is None


def test_explicit_entry_state_wins():
    model = {"entry_state": "B", "states": [{"id": "A"}, {"id": "B"}]}
    assert fv.build_index(model)[1] == "B"


def test_setup_path_is_ordered_from_entry(flow):
    by_id, _ = fv.build_index(flow)
    assert fv.resolve_setup_path("S3", by_id) == ["S0", "S1", "S2", "S3"]


def test_locks_are_cumulative_along_the_path(flow):
    by_id, _ = fv.build_index(flow)
    flow["states"][1]["locked_behaviours"] = ["reorder rounds"]
    by_id, _ = fv.build_index(flow)
    locks = fv.collect_locks(["S0", "S1", "S2", "S3"], by_id)
    assert locks == {"reorder rounds", "edit job description"}


def test_unreachable_state_is_not_reachable():
    model = {"states": [
        {"id": "S0", "requires": []},
        {"id": "ORPHAN", "requires": ["GHOST"]},
    ]}
    by_id, entry = fv.build_index(model)
    assert "ORPHAN" not in fv.reachable_states(by_id, entry)


# ── Anchoring ────────────────────────────────────────────────────────────────

def test_validate_anchors_and_reports(flow):
    cps = [_cp(precondition_state="S3")]
    summary = fv.validate(cps, flow)
    assert cps[0]["setup_path"] == ["S0", "S1", "S2", "S3"]
    assert cps[0]["precondition_state"] == "S3"
    assert summary["anchored"] == 1 and summary["unanchored"] == 0
    assert cps[0].get("review_status") is None


def test_validate_resolves_state_from_page_map(flow):
    """`pages` is author-supplied, so this is a lookup and not a guess."""
    cps = [_cp(page="Create Job")]
    fv.validate(cps, flow)
    assert cps[0]["precondition_state"] == "S2"


def test_unmapped_page_is_not_guessed(flow):
    cps = [_cp(page="Some Page Nobody Mapped")]
    fv.validate(cps, flow)
    assert "precondition_state" not in cps[0]
    assert cps[0]["review_status"] == "needs_design_flow"


# ── Flagging: never drop ─────────────────────────────────────────────────────

@pytest.mark.parametrize("cp_kwargs,code", [
    ({}, fv.E_NO_STATE),
    ({"precondition_state": "NOPE"}, fv.E_UNKNOWN_STATE),
])
def test_validate_flags_rather_than_drops(flow, cp_kwargs, code):
    cps = [_cp(**cp_kwargs)]
    summary = fv.validate(cps, flow)
    assert len(cps) == 1, "flow validation must never remove a checkpoint"
    assert cps[0]["review_status"] == "needs_design_flow"
    assert code in cps[0]["review_reason"]
    assert summary["by_reason"][code] == 1


def test_unreachable_state_is_flagged():
    model = {"states": [
        {"id": "S0", "requires": []},
        {"id": "ORPHAN", "requires": ["GHOST"]},
    ]}
    cps = [_cp(precondition_state="ORPHAN")]
    fv.validate(cps, model)
    assert fv.E_UNREACHABLE in cps[0]["review_reason"]


def test_existing_review_status_wins(flow):
    """"The document did not specify this well enough to execute" outranks
    "and it also has no starting point"."""
    cps = [_cp(review_status="needs_review", review_reason="original reason")]
    fv.validate(cps, flow)
    assert cps[0]["review_status"] == "needs_review"
    assert cps[0]["review_reason"] == "original reason"


# ── Locked behaviours ────────────────────────────────────────────────────────

def test_locked_behaviour_claimed_to_succeed_is_flagged(flow):
    cps = [_cp(precondition_state="S3", title="Edit job description after creation",
               expected="The updated description is saved successfully")]
    fv.validate(cps, flow)
    assert cps[0]["review_status"] == "needs_design_flow"
    assert cps[0]["flow_locked_behaviour"] == "edit job description"


def test_locked_behaviour_asserted_as_blocked_is_accepted(flow):
    """Testing that the system REFUSES a locked action is the correct test."""
    cps = [_cp(precondition_state="S3", title="Edit job description after creation",
               expected="The system blocks the edit and shows an error message")]
    fv.validate(cps, flow)
    assert cps[0].get("review_status") is None
    assert cps[0]["setup_path"] == ["S0", "S1", "S2", "S3"]


def test_lock_not_applied_before_its_state(flow):
    cps = [_cp(precondition_state="S2", title="Edit job description",
               expected="The description is saved")]
    fv.validate(cps, flow)
    assert cps[0].get("review_status") is None


# ── Fail-open ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model", [
    {},
    {"states": []},
    {"states": "not a list"},
    {"states": [{"no_id": 1}]},
    {"states": [{"id": "A"}, {"id": "B"}]},  # ambiguous entry
])
def test_unusable_model_fails_open(model):
    cps = [_cp(precondition_state="A")]
    assert fv.validate(cps, model) == {}
    assert cps[0].get("review_status") is None


def test_cycle_fails_open_rather_than_recursing():
    model = {"states": [
        {"id": "S0", "requires": []},
        {"id": "A", "requires": ["B"]},
        {"id": "B", "requires": ["A"]},
    ]}
    by_id, _ = fv.build_index(model)
    assert fv.resolve_setup_path("A", by_id) == []


def test_validate_never_raises_on_garbage_checkpoints(flow):
    cps = [{"precondition_state": "S3"}, {}, {"page": None}]
    fv.validate(cps, flow)  # must not raise
    assert len(cps) == 3


# ── Provider ─────────────────────────────────────────────────────────────────

def test_provider_returns_none_without_env(monkeypatch):
    monkeypatch.delenv("TDD_FLOW_MODEL_PATH", raising=False)
    assert fv.get_flow_model(None, 1) is None


def test_provider_returns_none_for_missing_file(monkeypatch):
    monkeypatch.setenv("TDD_FLOW_MODEL_PATH", "/nonexistent/flow.json")
    assert fv.get_flow_model(None, 1) is None


def test_provider_returns_none_for_malformed_json(monkeypatch, tmp_path):
    bad = tmp_path / "flow.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TDD_FLOW_MODEL_PATH", str(bad))
    assert fv.get_flow_model(None, 1) is None


def test_provider_loads_a_valid_model(monkeypatch, tmp_path, flow):
    good = tmp_path / "flow.json"
    good.write_text(json.dumps(flow), encoding="utf-8")
    monkeypatch.setenv("TDD_FLOW_MODEL_PATH", str(good))
    assert fv.get_flow_model(None, 1)["states"][0]["id"] == "S0"


# ── Prompt fragment ──────────────────────────────────────────────────────────

def test_no_flow_model_renders_nothing():
    """The guarantee that keeps the extraction prompt byte-identical for every
    project that has no flow model."""
    assert fv.render_flow_reference(None) == ""
    assert fv.render_flow_reference({}) == ""
    assert fv.render_flow_reference({"states": [{"id": "A"}, {"id": "B"}]}) == ""


def test_prompt_names_states_entry_and_locks(flow):
    text = fv.render_flow_reference(flow)
    assert "precondition_state" in text
    assert "Runs begin at S0" in text
    assert "edit job description" in text
    for sid in ("S0", "S1", "S2", "S3"):
        assert sid in text
