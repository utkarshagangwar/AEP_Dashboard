"""Unit tests for app.services.tdd_extraction.

Scope: the DETERMINISTIC parts of the pipeline — segmentation, the
testability gate's rules and its safety valve, the category contract, the
code-side variant-coverage backstop, dedupe, and the scorecard. No network,
no database, no LLM: every test that would otherwise reach a provider stubs
llm_router at the module boundary.

The behaviour these tests pin down is the answer to the original defect
report — "the agent extracts all SOW content as TDDs, but a TDD must test
platform functionality with positive, negative and edge cases". Concretely:

  * non-testable project content is excluded AND recorded (never silently
    dropped)                                    — test_zoning_*
  * a behaviour missing its category's required variants is flagged rather
    than silently accepted                      — test_variant_coverage_*
  * the gate fails open on every error path     — test_zoning_fails_open,
                                                  test_zoning_safety_valve
"""
from __future__ import annotations

import pytest

from app.services import tdd_extraction as tdd


def _stub_router(monkeypatch, replacement):
    """Swap the lazily-imported llm_router for this test only.

    BOTH forms are required, and patching only sys.modules is a trap: once
    any earlier test has really imported llm_router, the `app.services`
    package carries it as an ATTRIBUTE, and `from app.services import
    llm_router` resolves that attribute without consulting sys.modules at
    all. A test written with only the setitem passes in isolation and makes a
    live provider call inside the full suite.

    monkeypatch (not a bare assignment) so both are restored afterwards — a
    leaked stub would silently decide the outcome of every later test.
    """
    import sys

    monkeypatch.setattr("app.services.llm_router", replacement, raising=False)
    monkeypatch.setitem(sys.modules, "app.services.llm_router", replacement)


# ── Segmentation ─────────────────────────────────────────────────────────────

def test_split_segments_preserves_all_text():
    text = (
        "# Overview\nSome intro prose that is long enough to stand alone as a segment.\n\n"
        "# Functional Requirements\nThe user can create a job from the jobs list page.\n\n"
        "# Pricing\nThe engagement is billed monthly in arrears at the agreed rate card.\n"
    )
    segments = tdd.split_segments(text)
    assert len(segments) >= 3
    # Zoning may only SELECT text, never rewrite it: the concatenation of the
    # bodies must still contain every original line.
    joined = "\n".join(s["body"] for s in segments)
    for line in text.strip().splitlines():
        if line.strip():
            assert line in joined


def test_split_segments_merges_runt_fragments():
    text = "# Real Section\n" + ("Body line that is comfortably past the minimum. " * 4) + "\n# X\n"
    segments = tdd.split_segments(text)
    # The trailing one-word heading is too small to classify on its own and
    # must ride along with its neighbour rather than becoming a segment.
    assert all(s["char_count"] >= tdd._MIN_SEGMENT_CHARS or len(segments) == 1 for s in segments)


def test_split_segments_handles_text_with_no_headings():
    text = "A flat paragraph with no heading at all, as a hard_split part would be."
    segments = tdd.split_segments(text)
    assert len(segments) == 1
    assert segments[0]["heading"] is None
    assert segments[0]["body"] == text


# ── Stage 0: the testability gate ────────────────────────────────────────────

@pytest.mark.parametrize(
    "heading,expected_kind",
    [
        ("7. Commercial Terms", "commercial"),
        ("Project Timeline and Milestones", "schedule"),
        ("Out of Scope", "out_of_scope"),
        ("Assumptions and Dependencies", "assumptions"),
        ("Glossary of Terms", "glossary"),
        ("Document Revision History", "doc_control"),
        ("Team Structure", "resourcing"),
        ("Executive Summary", "background"),
    ],
)
def test_deterministic_gate_excludes_project_content(heading, expected_kind):
    segment = {
        "heading": heading,
        "body": f"{heading}\nStandard boilerplate paragraph with no product behaviour in it.",
        "char_count": 90,
    }
    verdict = tdd.deterministic_zone_verdict(segment)
    assert verdict is not None, f"{heading!r} should have been gated out"
    assert verdict[0] == expected_kind
    assert verdict[1]  # a reason is always recorded for the reviewer


def test_deterministic_gate_keeps_functional_headings():
    segment = {
        "heading": "4.2 Candidate Shortlisting",
        "body": "A recruiter can shortlist a candidate from the list view.",
        "char_count": 60,
    }
    assert tdd.deterministic_zone_verdict(segment) is None


def test_behaviour_marker_vetoes_a_heading_match():
    """An 'Assumptions' section that nevertheless specifies behaviour must
    NOT be gated out on its heading — a false exclusion loses a requirement
    permanently, which is the failure mode the whole gate must avoid."""
    segment = {
        "heading": "Assumptions",
        "body": (
            "Assumptions\nThe client provides test data. The system must reject a "
            "duplicate email address with a visible validation error."
        ),
        "char_count": 150,
    }
    assert tdd.deterministic_zone_verdict(segment) is None


def test_zoning_records_every_exclusion(monkeypatch):
    monkeypatch.setenv("TDD_ZONING", "0")  # deterministic rules only, no LLM
    segments = [
        {"heading": "Pricing", "body": "Pricing\n" + "Billed monthly. " * 10, "char_count": 160},
        {"heading": "Job Creation", "body": "Job Creation\n" + "A recruiter creates a job. " * 10, "char_count": 280},
    ]
    testable, excluded, _ = tdd.classify_zones(segments)
    assert [s["heading"] for s in testable] == ["Job Creation"]
    assert len(excluded) == 1
    entry = excluded[0]
    # Auditability contract: heading, kind, reason, size and which classifier
    # made the call. Without all five the gate cannot be reviewed.
    assert set(entry) == {"heading", "zone_kind", "reason", "char_count", "classifier"}
    assert entry["classifier"] == "deterministic"


def test_zoning_safety_valve_discards_an_over_aggressive_verdict(monkeypatch):
    """Excluding almost everything is far likelier to be a classifier fault
    than a document with no requirements — the verdict is thrown away and the
    whole part is extracted from."""
    monkeypatch.setenv("TDD_ZONING", "0")
    segments = [
        {"heading": "Pricing", "body": "Pricing\n" + "x" * 5000, "char_count": 5000},
        {"heading": "Glossary", "body": "Glossary\n" + "y" * 5000, "char_count": 5000},
        {"heading": "Job Creation", "body": "Job Creation\nz", "char_count": 20},
    ]
    testable, excluded, _ = tdd.classify_zones(segments)
    assert len(testable) == 3, "the over-broad exclusion should have been discarded"
    assert excluded == []


def test_zoning_fails_open_when_the_model_errors(monkeypatch):
    """An LLM failure must never delete content."""
    monkeypatch.setenv("TDD_ZONING", "1")

    class _Boom:
        @staticmethod
        def complete_json_complete(*_args, **_kwargs):
            raise RuntimeError("provider down")

    # _stub_router patches both the package attribute and
    # sys.modules, and monkeypatch restores both. The bare
    # `sys.modules[...] = _Boom` this used to do was never undone, so a
    # provider that always raises stayed installed for every test that ran
    # after this one in the same session.
    _stub_router(monkeypatch, _Boom)

    segments = [
        {"heading": "Ambiguous Section", "body": "Some text that needs a judgement call.", "char_count": 120},
    ]
    testable, excluded, _ = tdd.classify_zones(segments)
    assert len(testable) == 1
    assert excluded == []


# ── The category contract ────────────────────────────────────────────────────

def test_every_category_declares_a_usable_contract():
    for code, entry in tdd.CATEGORIES.items():
        assert entry["label"], code
        assert entry["when"], code
        assert entry["requires"], code
        assert set(entry["requires"]) <= set(tdd.TEST_TYPES), code
        assert "positive" in entry["requires"], code
        # Anything that requires a negative/edge variant must also tell the
        # model what to probe, or the requirement is unactionable.
        if "negative" in entry["requires"]:
            assert entry["negative"], code
        if "edge" in entry["requires"]:
            assert entry["edge"], code


def test_only_visual_layout_is_positive_only():
    positive_only = {c for c in tdd.CATEGORIES if tdd.category_requires(c) == ("positive",)}
    assert positive_only == {"visual_layout"}


def test_category_reference_is_rendered_for_the_prompt():
    reference = tdd.render_category_reference()
    for code in tdd.CATEGORIES:
        assert f'"{code}"' in reference
    assert "NEGATIVE probes" in reference
    assert "EDGE probes" in reference


def test_extraction_prompt_carries_the_contract_and_the_exclusion_rule():
    prompt = tdd.build_extraction_system(part_label="part 2 of 5")
    assert "EXTRACT BEHAVIOURS, NOT TEXT" in prompt
    assert "EVERY BEHAVIOUR GETS VARIANTS" in prompt
    assert "ai_untrusted_input" in prompt
    assert "part 2 of 5" in prompt


def test_ai_categories_cover_the_model_specific_risks():
    for expected in (
        "ai_prompt_config",
        "ai_generation",
        "ai_untrusted_input",
        "ai_scoring",
        "ai_context",
        "ai_explainability",
    ):
        assert expected in tdd.AI_CATEGORIES


# ── Stage 4: the code-side variant backstop ──────────────────────────────────

def _cp(test_type: str, objective: str = "does the thing", **extra) -> dict:
    return {"type": "functional", "test_type": test_type, "objective": objective, **extra}


def test_variant_coverage_flags_a_missing_negative():
    checkpoints = [_cp("positive")]
    result = tdd.check_variant_coverage(
        checkpoints, category="input_validation", behaviour_key="create-job"
    )
    # Not dropped, not silently accepted — flagged on the anchor checkpoint.
    assert len(result) == 1
    assert result[0]["coverage_gap"] == ["edge", "negative"]


def test_variant_coverage_is_silent_when_the_contract_is_met():
    checkpoints = [_cp("positive"), _cp("negative", "is rejected"), _cp("edge", "at the boundary")]
    result = tdd.check_variant_coverage(
        checkpoints, category="input_validation", behaviour_key="create-job"
    )
    assert all("coverage_gap" not in cp for cp in result)


def test_variant_coverage_does_not_demand_variants_of_a_visual_checkpoint():
    checkpoints = [_cp("positive")]
    result = tdd.check_variant_coverage(
        checkpoints, category="visual_layout", behaviour_key="header-logo"
    )
    assert "coverage_gap" not in result[0]


def test_unknown_category_falls_back_rather_than_raising():
    assert tdd.category_requires("not_a_real_category") == ("positive",)
    assert tdd._normalize_category("not_a_real_category") == tdd._FALLBACK_CATEGORY
    assert tdd._normalize_category(None) == tdd._FALLBACK_CATEGORY


# ── Stage 5: dedupe and scorecard ────────────────────────────────────────────

def test_dedupe_collapses_only_exact_repeats():
    checkpoints = [
        {"behaviour_key": "create-job", "test_type": "positive", "objective": "A job is created"},
        {"behaviour_key": "create-job", "test_type": "positive", "objective": "A job is created"},
        # Same behaviour and type, DIFFERENT case — an input form has many
        # distinct negative cases and they must all survive.
        {"behaviour_key": "create-job", "test_type": "negative", "objective": "Empty title rejected"},
        {"behaviour_key": "create-job", "test_type": "negative", "objective": "Duplicate title rejected"},
    ]
    kept = tdd.dedupe(checkpoints)
    assert len(kept) == 3
    assert kept[0]["objective"] == "A job is created"  # first occurrence wins


def test_scorecard_reports_the_headline_ratio():
    checkpoints = [
        {"test_type": "positive", "category": "crud", "grounding": "stated"},
        {"test_type": "negative", "category": "crud", "grounding": "derived"},
        {"test_type": "edge", "category": "crud", "grounding": "derived"},
        {"test_type": "positive", "category": "ai_untrusted_input", "grounding": "stated"},
    ]
    excluded = [{"zone_kind": "commercial"}, {"zone_kind": "schedule"}]
    card = tdd.scorecard(checkpoints, excluded)

    assert card["total_checkpoints"] == 4
    assert card["by_test_type"] == {"positive": 2, "negative": 1, "edge": 1}
    assert card["negative_edge_ratio"] == 0.5
    assert card["by_grounding"] == {"stated": 2, "derived": 2}
    assert card["ai_category_checkpoints"] == 1
    assert card["excluded_zone_count"] == 2
    assert card["excluded_zone_kinds"] == ["commercial", "schedule"]


def test_scorecard_on_an_empty_part_does_not_divide_by_zero():
    card = tdd.scorecard([], [{"zone_kind": "glossary"}])
    assert card["total_checkpoints"] == 0
    assert card["negative_edge_ratio"] == 0.0


def test_scorecard_surfaces_coverage_gaps():
    checkpoints = [
        {
            "test_type": "positive",
            "category": "authorization",
            "grounding": "stated",
            "behaviour_key": "delete-user",
            "coverage_gap": ["negative"],
        }
    ]
    card = tdd.scorecard(checkpoints, [])
    assert card["coverage_gaps"] == [
        {"behaviour_key": "delete-user", "category": "authorization", "missing": ["negative"]}
    ]


# ── The flat-list backstop (video path parity) ───────────────────────────────

def _observed(test_type, key, title, category="crud"):
    return {
        "type": "functional",
        "title": title,
        "description": f"# Objective\n{title}",
        "objective": title,
        "instructions": ["Click the button"],
        "test_type": test_type,
        "grounding": "stated" if test_type == "positive" else "derived",
        "category": category,
        "behaviour_key": key,
    }


def test_backstop_flags_a_missing_variant_on_a_flat_list(monkeypatch):
    """Stages 4/4c were SOW-only. A walkthrough-derived behaviour missing a
    required variant must be flagged the same way a document-derived one is."""
    monkeypatch.delenv("TDD_VARIANT_CAP", raising=False)
    # crud requires positive + negative + edge; only positive is present.
    checkpoints = [_observed("positive", "create-a-job", "Create a job")]
    out = tdd.apply_variant_backstop(checkpoints)
    assert out[0]["coverage_gap"] == ["edge", "negative"]


def test_backstop_is_silent_when_the_contract_is_met():
    checkpoints = [
        _observed("positive", "create-a-job", "Create a job"),
        _observed("negative", "create-a-job", "Reject an empty title"),
        _observed("edge", "create-a-job", "Create at the title length limit"),
    ]
    out = tdd.apply_variant_backstop(checkpoints)
    assert all("coverage_gap" not in cp for cp in out)
    assert len(out) == 3


def test_backstop_handles_several_behaviours_independently():
    checkpoints = [
        _observed("positive", "create-a-job", "Create a job"),
        _observed("positive", "archive-a-job", "Archive a job"),
        _observed("negative", "create-a-job", "Reject an empty title"),
        _observed("edge", "create-a-job", "Create at the limit"),
    ]
    out = tdd.apply_variant_backstop(checkpoints)
    by_key = {cp["behaviour_key"]: cp for cp in out if cp["test_type"] == "positive"}
    assert "coverage_gap" not in by_key["create-a-job"]
    assert by_key["archive-a-job"]["coverage_gap"] == ["edge", "negative"]


def test_backstop_keeps_a_behaviours_variants_together_in_order():
    """A group is emitted at the position of its first member, so a
    behaviour's variants stay adjacent even when they arrived interleaved."""
    checkpoints = [
        _observed("positive", "create-a-job", "Create a job"),
        _observed("positive", "archive-a-job", "Archive a job"),
        _observed("negative", "create-a-job", "Reject an empty title"),
    ]
    out = tdd.apply_variant_backstop(checkpoints)
    keys = [cp["behaviour_key"] for cp in out]
    assert keys == ["create-a-job", "create-a-job", "archive-a-job"]


def test_backstop_passes_through_checkpoints_with_no_behaviour():
    """A visual checkpoint has no category contract to enforce and must not
    be forced into a group or dropped."""
    visual = {"type": "visual", "title": "Logo in the header", "description": "Logo in the header"}
    out = tdd.apply_variant_backstop([visual, _observed("positive", "create-a-job", "Create a job")])
    assert visual in out
    assert len(out) == 2


def test_backstop_applies_the_variant_cap_too(monkeypatch):
    monkeypatch.delenv("TDD_VARIANT_CAP", raising=False)
    checkpoints = [_observed("positive", "create-a-job", "Create a job")]
    checkpoints += [
        _observed("negative", "create-a-job", f"Reject case {i}")
        for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR + 3)
    ]
    checkpoints.append(_observed("edge", "create-a-job", "At the limit"))
    out = tdd.apply_variant_backstop(checkpoints)
    assert len(out) == tdd._MAX_VARIANTS_PER_BEHAVIOUR
    assert {cp["test_type"] for cp in out} == {"positive", "negative", "edge"}


def test_backstop_is_a_noop_on_an_empty_list():
    assert tdd.apply_variant_backstop([]) == []


def test_classify_and_expand_runs_the_backstop(monkeypatch):
    """End-to-end on the video path: the expansion prompt ASKS for the
    required variants; this proves the code checks that they arrived rather
    than trusting the model's claim (P5)."""
    class _Router:
        @staticmethod
        def complete_json_complete(prompt, *, system, max_tokens):
            # Categorised, but no derived cases returned at all — the exact
            # thing that used to pass unnoticed on this path.
            return _Reply({
                "assignments": [{"index": 0, "category": "crud", "behaviour_key": "create-a-job"}],
                "derived": [],
            })

    _stub_router(monkeypatch, _Router)

    def _no_repairs(prompt, *, system, max_tokens):
        return _Reply({"repairs": []})

    from app.services import design_ingest

    monkeypatch.setattr(design_ingest, "_complete_via_brain", _no_repairs)

    observed = [{
        "type": "functional",
        "title": "Create a job",
        "description": "# Objective\nCreate a job",
        "objective": "Create a job from the jobs list page",
        "instructions": ["Click Create Job"],
    }]
    out, model_used = tdd.classify_and_expand(observed)

    assert out[0]["category"] == "crud"
    assert out[0]["test_type"] == "positive"
    # Observed on screen is the strongest grounding there is.
    assert out[0]["grounding"] == "stated"
    assert out[0]["coverage_gap"] == ["edge", "negative"]
    assert model_used


def test_classify_and_expand_never_fails_a_digested_video(monkeypatch):
    """A video that digested successfully must not be failed by enrichment,
    now that enrichment makes two provider calls instead of one."""
    class _Boom:
        @staticmethod
        def complete_json_complete(*_args, **_kwargs):
            raise RuntimeError("provider down")

    _stub_router(monkeypatch, _Boom)

    observed = [{
        "type": "functional",
        "title": "Create a job",
        "description": "# Objective\nCreate a job",
        "objective": "Create a job from the jobs list page",
        "instructions": ["Click Create Job"],
    }]
    out, model_used = tdd.classify_and_expand(observed)
    assert out == observed
    assert model_used == ""


# ── Stage 4c: variant volume cap ─────────────────────────────────────────────

def _variant(test_type, priority, title):
    return {
        "type": "functional",
        "title": title,
        "description": f"# Objective\n{title}",
        "objective": title,
        "instructions": ["Do the thing"],
        "test_type": test_type,
        "priority": priority,
        "grounding": "stated",
        "category": "input_validation",
        "behaviour_key": "create-a-job",
    }


def test_cap_is_a_noop_below_the_ceiling(monkeypatch):
    monkeypatch.delenv("TDD_VARIANT_CAP", raising=False)
    cps = [_variant("negative", "regression", f"case {i}") for i in range(3)]
    assert tdd.cap_variants(cps, behaviour_key="create-a-job") == cps


def test_cap_keeps_one_of_every_test_type_before_anything_else(monkeypatch):
    """The whole point of the ordering: dropping the only edge case to keep an
    eighth negative would gut the coverage this pipeline exists to produce."""
    cps = [_variant("positive", "smoke", "happy path")]
    cps += [_variant("negative", "smoke", f"negative {i}") for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR + 2)]
    # The only edge case, and the lowest priority in the set — it survives
    # anyway, because rule 1 runs before priority is consulted.
    cps.append(_variant("edge", "regression", "the lone edge case"))

    survivors = tdd.cap_variants(cps, behaviour_key="create-a-job")

    assert len(survivors) == tdd._MAX_VARIANTS_PER_BEHAVIOUR
    types = {cp["test_type"] for cp in survivors}
    assert types == {"positive", "negative", "edge"}
    assert any(cp["title"] == "the lone edge case" for cp in survivors)


def test_cap_prefers_higher_priority_variants():
    cps = [_variant("positive", "smoke", "happy path")]
    cps += [_variant("negative", "regression", f"regression {i}") for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR)]
    cps += [_variant("negative", "smoke", f"smoke {i}") for i in range(3)]

    survivors = tdd.cap_variants(cps, behaviour_key="create-a-job")
    titles = {cp["title"] for cp in survivors}

    assert len(survivors) == tdd._MAX_VARIANTS_PER_BEHAVIOUR
    for i in range(3):
        assert f"smoke {i}" in titles, "a smoke test must never be dropped for a regression one"


def test_cap_returns_survivors_in_document_order():
    """Selection is by priority; presentation is by document order."""
    cps = [_variant("negative", "regression", f"case {i}") for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR + 4)]
    cps[0] = _variant("positive", "smoke", "case 0")

    survivors = tdd.cap_variants(cps, behaviour_key="create-a-job")
    order = [int(cp["title"].split()[-1]) for cp in survivors]
    assert order == sorted(order)


def test_cap_records_how_many_it_dropped():
    """No silent caps: a behaviour that produced 8 must be distinguishable
    from one that produced 20 and kept 8."""
    extra = 5
    cps = [_variant("positive", "smoke", "happy path")]
    cps += [
        _variant("negative", "regression", f"case {i}")
        for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR + extra - 1)
    ]
    survivors = tdd.cap_variants(cps, behaviour_key="create-a-job")
    anchor = next(cp for cp in survivors if cp["test_type"] == "positive")
    assert anchor["capped_variants"] == extra
    assert tdd.scorecard(survivors, [])["capped_variants"] == extra


def test_cap_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TDD_VARIANT_CAP", "0")
    cps = [_variant("negative", "regression", f"case {i}") for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR + 6)]
    assert tdd.cap_variants(cps, behaviour_key="create-a-job") == cps


def test_cap_tolerates_an_unrecognised_priority():
    """An unknown priority sorts last rather than raising — a malformed field
    must not take down a part's extraction."""
    cps = [_variant("positive", "smoke", "happy path")]
    cps += [_variant("negative", "not-a-priority", f"case {i}") for i in range(tdd._MAX_VARIANTS_PER_BEHAVIOUR + 2)]
    survivors = tdd.cap_variants(cps, behaviour_key="create-a-job")
    assert len(survivors) == tdd._MAX_VARIANTS_PER_BEHAVIOUR
    assert any(cp["title"] == "happy path" for cp in survivors)


# ── Stage 4b: coverage-gap repair ────────────────────────────────────────────

class _Reply:
    """Stand-in for a design_ingest._complete_via_brain result."""

    def __init__(self, parsed_json):
        self.parsed_json = parsed_json
        self.model_used = "stub-model"


def _stub_repair_reply(monkeypatch, parsed_json):
    """Patch the provider call repair_coverage_gaps makes, and capture the
    prompt so the payload it sends can be asserted on."""
    captured = {}

    def _fake(prompt, *, system, max_tokens):
        captured["prompt"] = prompt
        captured["system"] = system
        return _Reply(parsed_json)

    from app.services import design_ingest

    monkeypatch.setattr(design_ingest, "_complete_via_brain", _fake)
    return captured


def _gapped_behaviour(missing):
    """One behaviour with a happy path and an unfilled coverage gap."""
    return [
        {
            "type": "functional",
            "title": "Create a job",
            "description": "Role/Objective/... rendered markdown",
            "objective": "Create a job from the jobs list page",
            "instructions": ["Open the jobs list page", "Click Create Job"],
            "notes": [],
            "test_type": "positive",
            "grounding": "stated",
            "category": "input_validation",
            "behaviour_key": "create-a-job",
            "coverage_gap": list(missing),
        }
    ]


def _repair_item(behaviour_key, test_type, title="Reject an empty job title"):
    return {
        "behaviour_key": behaviour_key,
        "test_type": test_type,
        "title": title,
        "objective": "Submit the create-job form with no title and confirm it is rejected",
        "instructions": [
            "Open the jobs list page",
            "Click Create Job",
            "Leave the title empty and submit",
            "Confirm a visible error is shown and no job is created",
        ],
        "notes": [],
        "priority": "regression",
    }


def test_repair_fills_the_gap_and_clears_the_flag(monkeypatch):
    monkeypatch.delenv("TDD_GAP_REPAIR", raising=False)
    checkpoints = _gapped_behaviour(["negative"])
    _stub_repair_reply(monkeypatch, {"repairs": [_repair_item("create-a-job", "negative")]})

    repaired, model = tdd.repair_coverage_gaps(checkpoints)

    assert model == "stub-model"
    assert [cp["test_type"] for cp in repaired] == ["positive", "negative"]
    # The gap is closed, so the flag must go — leaving it would report a hole
    # that no longer exists.
    assert "coverage_gap" not in repaired[0]
    # Repaired variants are REASONED, never read out of the document. Calling
    # one "stated" would make a spec gap look like a product defect in triage.
    assert repaired[1]["grounding"] == "derived"
    assert repaired[1]["behaviour_key"] == "create-a-job"
    assert repaired[1]["category"] == "input_validation"


def test_repair_keeps_the_flag_for_a_variant_it_could_not_write(monkeypatch):
    """The point of the stage: coverage is recomputed from what came back,
    never from the model's claim to have done the work."""
    checkpoints = _gapped_behaviour(["negative", "edge"])
    _stub_repair_reply(monkeypatch, {"repairs": [_repair_item("create-a-job", "negative")]})

    repaired, _ = tdd.repair_coverage_gaps(checkpoints)

    assert repaired[0]["coverage_gap"] == ["edge"]
    assert [cp["test_type"] for cp in repaired] == ["positive", "negative"]


def test_repair_ignores_variants_that_were_not_missing(monkeypatch):
    """A model re-supplying the happy path must not create a duplicate."""
    checkpoints = _gapped_behaviour(["negative"])
    _stub_repair_reply(monkeypatch, {
        "repairs": [
            _repair_item("create-a-job", "positive", title="Create a job successfully"),
            _repair_item("create-a-job", "negative"),
        ]
    })

    repaired, _ = tdd.repair_coverage_gaps(checkpoints)
    assert [cp["test_type"] for cp in repaired] == ["positive", "negative"]


def test_repair_drops_a_behaviour_it_was_never_asked_about(monkeypatch):
    """Silently widening the output is how the original 'everything becomes a
    TDD' defect behaved."""
    checkpoints = _gapped_behaviour(["negative"])
    _stub_repair_reply(monkeypatch, {
        "repairs": [_repair_item("some-other-behaviour", "negative")]
    })

    repaired, _ = tdd.repair_coverage_gaps(checkpoints)
    assert len(repaired) == 1
    assert repaired[0]["coverage_gap"] == ["negative"]


def test_repair_never_fails_a_successful_parse(monkeypatch):
    """A provider failure here must cost the gap flag, never the checkpoints."""
    checkpoints = _gapped_behaviour(["negative"])

    def _boom(prompt, *, system, max_tokens):
        raise RuntimeError("provider down")

    from app.services import design_ingest

    monkeypatch.setattr(design_ingest, "_complete_via_brain", _boom)

    repaired, model = tdd.repair_coverage_gaps(checkpoints)
    assert repaired == checkpoints
    assert model == ""
    assert repaired[0]["coverage_gap"] == ["negative"]


def test_repair_makes_no_call_when_there_is_nothing_to_repair(monkeypatch):
    called = []

    def _fake(prompt, *, system, max_tokens):
        called.append(prompt)
        return _Reply({"repairs": []})

    from app.services import design_ingest

    monkeypatch.setattr(design_ingest, "_complete_via_brain", _fake)

    clean = _gapped_behaviour([])
    clean[0].pop("coverage_gap")
    repaired, model = tdd.repair_coverage_gaps(clean)
    assert repaired == clean
    assert model == ""
    assert called == [], "no gaps must mean no tokens spent"


def test_repair_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TDD_GAP_REPAIR", "0")
    checkpoints = _gapped_behaviour(["negative"])
    _stub_repair_reply(monkeypatch, {"repairs": [_repair_item("create-a-job", "negative")]})

    repaired, model = tdd.repair_coverage_gaps(checkpoints)
    assert repaired == checkpoints
    assert model == ""
    assert repaired[0]["coverage_gap"] == ["negative"], "the flag is the fallback behaviour"


def test_repair_sends_the_existing_case_so_real_labels_are_reused(monkeypatch):
    """A repaired test that invents its own field names cannot be executed."""
    checkpoints = _gapped_behaviour(["negative"])
    captured = _stub_repair_reply(monkeypatch, {"repairs": []})

    tdd.repair_coverage_gaps(checkpoints)

    assert "Click Create Job" in captured["prompt"]
    assert "input_validation" in captured["prompt"]
    assert "negative" in captured["prompt"]
    # The category's probes travel with the request, so the model is told what
    # to attempt rather than guessing.
    assert "CATEGORY CONTRACT" in captured["system"]


def test_repair_caps_how_many_behaviours_one_call_covers(monkeypatch):
    """Above the cap the extra behaviours keep their flag — and the log names
    them, because a silent cap reads as 'everything was repaired'."""
    checkpoints = []
    for i in range(tdd._MAX_REPAIR_BEHAVIOURS + 3):
        cp = _gapped_behaviour(["negative"])[0]
        cp["behaviour_key"] = f"behaviour-{i}"
        checkpoints.append(cp)
    captured = _stub_repair_reply(monkeypatch, {"repairs": []})

    tdd.repair_coverage_gaps(checkpoints)

    assert f"behaviour-{tdd._MAX_REPAIR_BEHAVIOURS - 1}" in captured["prompt"]
    assert f"behaviour-{tdd._MAX_REPAIR_BEHAVIOURS}" not in captured["prompt"]
    assert checkpoints[-1]["coverage_gap"] == ["negative"]


# ── Stage 6: cross-part reconciliation ───────────────────────────────────────

def _part(part_number, *checkpoints):
    return {"part_number": part_number, "checkpoints": list(checkpoints)}


def _behaviour_cp(objective, *, test_type="positive", key="create-a-job", title="Create a job"):
    return {
        "type": "functional",
        "title": title,
        "description": f"# Objective\n{objective}",
        "objective": objective,
        "instructions": ["Open the jobs list page", "Click Create Job"],
        "test_type": test_type,
        "grounding": "stated",
        "category": "crud",
        "behaviour_key": key,
    }


def test_reconcile_merges_the_same_behaviour_stated_in_two_parts(monkeypatch):
    """The duplicate-skills complaint: a feature described in a summary
    section and again in a detail section."""
    monkeypatch.delenv("TDD_RECONCILE", raising=False)
    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page")),
        _part(4, _behaviour_cp("Create a job from the jobs list", key="job-creation")),
    ]
    result = tdd.reconcile_across_parts(parts)

    assert len(result.checkpoints) == 1
    assert result.merged_count == 1
    # First occurrence wins, so document order is stable and re-analysing a
    # later part cannot reshuffle the list.
    assert result.checkpoints[0]["objective"] == "Create a job from the jobs list page"
    # Nothing silently lost: the survivor records where else it was stated.
    assert result.checkpoints[0]["merged_from_parts"] == [4]
    # And the caller is told which of part 4's checkpoints not to make a
    # Skill for.
    assert result.absorbed == {4: {0}}


def test_reconcile_merges_across_differently_named_behaviours(monkeypatch):
    """Matching on behaviour_key alone would miss the common case — two parts
    naming the same behaviour differently is precisely what has to merge."""
    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page", key="create-a-job")),
        _part(2, _behaviour_cp("Create a job from the jobs list page", key="totally-different-slug")),
    ]
    result = tdd.reconcile_across_parts(parts)
    assert len(result.checkpoints) == 1


def test_reconcile_keeps_genuinely_different_tests():
    """Wrongly merging silently deletes a test, and the thing that would have
    reported it is the test that no longer exists. Different objectives stay."""
    parts = [
        _part(1, _behaviour_cp("Reject a job with an empty title", test_type="negative")),
        _part(3, _behaviour_cp("Reject a job with a duplicate title", test_type="negative")),
    ]
    result = tdd.reconcile_across_parts(parts)
    assert len(result.checkpoints) == 2
    assert result.merged_count == 0


def test_reconcile_never_merges_across_test_types():
    """A positive absorbing a negative would delete the negative coverage this
    whole pipeline exists to produce."""
    objective = "Create a job from the jobs list page"
    parts = [
        _part(1, _behaviour_cp(objective, test_type="positive")),
        _part(2, _behaviour_cp(objective, test_type="negative")),
    ]
    result = tdd.reconcile_across_parts(parts)
    assert len(result.checkpoints) == 2


def test_reconcile_never_merges_within_a_single_part():
    """Stage 5 already deduped inside a part, so anything still there is
    deliberately distinct — a rich behaviour legitimately has several near-
    worded negative cases."""
    objective = "Reject a job with an empty title"
    parts = [_part(1, _behaviour_cp(objective, test_type="negative"),
                   _behaviour_cp(objective, test_type="negative"))]
    result = tdd.reconcile_across_parts(parts)
    assert len(result.checkpoints) == 2
    assert result.merged_count == 0


def test_reconcile_is_a_noop_for_a_single_part_document():
    parts = [_part(1, _behaviour_cp("Create a job from the jobs list page"))]
    result = tdd.reconcile_across_parts(parts)
    assert len(result.checkpoints) == 1
    assert result.absorbed == {}
    assert result.model_used == ""


def test_reconcile_makes_no_model_call_until_the_document_is_complete(monkeypatch):
    """Merging is deterministic and free; only the naming pass costs tokens,
    and it must not run once per part."""
    called = []

    class _Router:
        @staticmethod
        def complete_json_complete(prompt, *, system, max_tokens):
            called.append(prompt)
            return _Reply({"mapping": []})

    _stub_router(monkeypatch, _Router)

    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page")),
        _part(2, _behaviour_cp("Archive a job from the job detail page", key="archive-a-job")),
    ]
    tdd.reconcile_across_parts(parts, finalize=False)
    assert called == []

    tdd.reconcile_across_parts(parts, finalize=True)
    assert len(called) == 1


def test_reconcile_naming_pass_only_accepts_keys_it_sent(monkeypatch):
    """A canonical name the model invented wholesale must not replace a real
    one — same rule as sow_drafting's heading consolidation."""
    class _Router:
        @staticmethod
        def complete_json_complete(prompt, *, system, max_tokens):
            return _Reply({"mapping": [
                {"from": "create-a-job", "to": "a-name-nobody-sent"},
                {"from": "job-creation", "to": "create-a-job"},
            ]})

    _stub_router(monkeypatch, _Router)

    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page", key="create-a-job")),
        _part(2, _behaviour_cp("Archive a job from the detail page", key="job-creation")),
    ]
    result = tdd.reconcile_across_parts(parts, finalize=True)
    keys = {cp["behaviour_key"] for cp in result.checkpoints}
    assert "a-name-nobody-sent" not in keys
    assert keys == {"create-a-job"}


def test_reconcile_falls_back_to_concatenation_when_naming_fails(monkeypatch):
    """A failed tidying pass must never cost a document its checkpoints."""
    class _Boom:
        @staticmethod
        def complete_json_complete(*_args, **_kwargs):
            raise RuntimeError("provider down")

    _stub_router(monkeypatch, _Boom)

    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page", key="create-a-job")),
        _part(2, _behaviour_cp("Archive a job from the detail page", key="archive-a-job")),
    ]
    result = tdd.reconcile_across_parts(parts, finalize=True)
    assert len(result.checkpoints) == 2
    assert {cp["behaviour_key"] for cp in result.checkpoints} == {"create-a-job", "archive-a-job"}


def test_reconcile_can_be_disabled(monkeypatch):
    monkeypatch.setenv("TDD_RECONCILE", "0")
    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page")),
        _part(2, _behaviour_cp("Create a job from the jobs list page")),
    ]
    result = tdd.reconcile_across_parts(parts)
    assert len(result.checkpoints) == 2, "disabled means the previous concatenation behaviour"
    assert result.absorbed == {}


def test_reconcile_leaves_no_private_bookkeeping_in_the_output():
    """_part_number is an internal marker; it must never reach storage."""
    parts = [
        _part(1, _behaviour_cp("Create a job from the jobs list page")),
        _part(2, _behaviour_cp("Archive a job from the detail page", key="archive-a-job")),
    ]
    result = tdd.reconcile_across_parts(parts)
    assert all("_part_number" not in cp for cp in result.checkpoints)


# ── Extraction-quality gate (spec §10) ───────────────────────────────────────

def _typed(test_type, **extra):
    return {"test_type": test_type, "grounding": "stated", **extra}


def test_ratio_gate_warns_on_happy_path_only_output():
    """The regression the whole module exists to catch: extraction succeeds
    but produces only positive cases, which is what the pre-v2 pipeline did
    by construction."""
    card = tdd.scorecard([_typed("positive") for _ in range(6)], [])
    warning = tdd.ratio_gate_warning(card)
    assert warning is not None
    assert "0%" in warning
    # Must say the checkpoints survive: this is a quality signal, not a
    # failure, and a warning that reads like data loss gets escalated wrongly.
    assert "kept" in warning


def test_ratio_gate_silent_when_coverage_is_healthy():
    checkpoints = [_typed("positive"), _typed("positive"), _typed("negative"), _typed("edge")]
    card = tdd.scorecard(checkpoints, [])
    assert card["negative_edge_ratio"] == 0.5
    assert tdd.ratio_gate_warning(card) is None


def test_ratio_gate_silent_exactly_at_the_gate():
    """The gate is >=, so a part sitting exactly on the threshold passes.
    Warning at the boundary would fire on documents that are fine."""
    checkpoints = [_typed("positive")] * 3 + [_typed("negative"), _typed("edge")]
    card = tdd.scorecard(checkpoints, [])
    assert card["negative_edge_ratio"] == 0.4
    assert tdd.ratio_gate_warning(card) is None


def test_ratio_gate_silent_on_a_sample_too_small_to_judge():
    """Two positives is 0% — technically below the gate, statistically
    meaningless. Warning here trains everyone to ignore the warning."""
    card = tdd.scorecard([_typed("positive"), _typed("positive")], [])
    assert card["negative_edge_ratio"] == 0.0
    assert tdd.ratio_gate_warning(card) is None


def test_ratio_gate_silent_on_a_fully_non_testable_part():
    """A pricing or timeline section correctly yields nothing. That is the
    testability gate working, not extraction drifting."""
    card = tdd.scorecard([], [{"zone_kind": "commercial"}])
    assert tdd.ratio_gate_warning(card) is None
    assert tdd.ratio_gate_warning(None) is None
    assert tdd.ratio_gate_warning({}) is None


# ── Config flags ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [("0", False), ("false", False), ("no", False), ("1", True), ("", True)])
def test_flags_are_opt_out(monkeypatch, value, expected):
    monkeypatch.setenv("TDD_EXTRACTION_V2", value)
    assert tdd.v2_enabled() is expected


def test_flags_default_to_enabled(monkeypatch):
    monkeypatch.delenv("TDD_EXTRACTION_V2", raising=False)
    monkeypatch.delenv("TDD_ZONING", raising=False)
    monkeypatch.delenv("TDD_DERIVED_AS_SKILLS", raising=False)
    assert tdd.v2_enabled()
    assert tdd.zoning_enabled()
    assert tdd.derived_as_skills()
