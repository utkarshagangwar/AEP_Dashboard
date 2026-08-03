"""Under-specified requirements are FLAGGED, never dropped.

The regression: `_validate_checkpoint` used to `return None` whenever a
functional checkpoint lacked instructions or an objective. The requirement
then existed nowhere — not in the checkpoint list, not in the skills list,
not in any count the UI shows — so a document full of vague requirements
looked identically "fully parsed" to one that was genuinely complete.

Nothing is marked ready ambiguously: the flag is derived from the evidence
(are there actually steps?) rather than trusted from the model's own claim.
"""
from __future__ import annotations

from app.services.design_ingest import _validate_checkpoint, render_skill_markdown


def _functional(**kw):
    base = {"type": "functional", "title": "Demo List Edit"}
    base.update(kw)
    return base


# ── Flag, don't drop ─────────────────────────────────────────────────────────

def test_a_requirement_with_no_steps_is_kept_and_flagged():
    cp = _validate_checkpoint(_functional(objective="The row saves."))

    assert cp is not None, "an under-specified requirement was dropped"
    assert cp["review_status"] == "needs_review"
    assert "no executable steps" in cp["review_reason"]
    assert cp["instructions"], "a placeholder step must exist so the skill is renderable"


def test_a_requirement_with_only_a_title_is_kept_and_flagged():
    cp = _validate_checkpoint(_functional())

    assert cp is not None
    assert cp["review_status"] == "needs_review"
    assert cp["title"] == "Demo List Edit"


def test_a_fully_specified_requirement_is_not_flagged():
    cp = _validate_checkpoint(_functional(
        objective="A new job appears in the list.",
        instructions=["Click Create Job.", "Enter a title.", "Click Submit."],
    ))

    assert cp is not None
    assert cp["review_status"] is None
    assert cp["review_reason"] is None
    assert len(cp["instructions"]) == 3


def test_a_completely_empty_checkpoint_is_still_dropped():
    """Flagging is for requirements that exist but are vague — not for
    nothing at all."""
    assert _validate_checkpoint({"type": "functional"}) is None
    assert _validate_checkpoint({"type": "functional", "title": "  "}) is None


def test_a_non_checkpoint_is_still_rejected():
    assert _validate_checkpoint("not a dict") is None
    assert _validate_checkpoint({"type": "nonsense", "title": "x"}) is None


# ── The model's own claim is honoured, but not trusted blindly ───────────────

def test_model_supplied_needs_design_flow_is_preserved():
    cp = _validate_checkpoint(_functional(
        objective="The candidate reaches the report screen.",
        instructions=["Open the report."],
        review_status="needs_design_flow",
        review_reason="The document names the report screen but no route to it.",
    ))

    assert cp["review_status"] == "needs_design_flow"
    assert "no route to it" in cp["review_reason"]


def test_a_claim_of_ready_cannot_override_missing_steps():
    """A model that says 'ready' while omitting the steps must not get a
    ready skill — the whole point is that nothing is marked done ambiguously."""
    cp = _validate_checkpoint(_functional(
        objective="It works.", review_status="ready",
    ))
    assert cp["review_status"] == "needs_review"


def test_an_unknown_review_status_degrades_to_ready():
    """An unrecognised value must not flag an entire document; the
    evidence-based check above still catches genuinely missing steps."""
    cp = _validate_checkpoint(_functional(
        objective="A new job appears.",
        instructions=["Click Submit."],
        review_status="banana",
    ))
    assert cp["review_status"] is None


def test_visual_checkpoints_carry_the_flag_too():
    cp = _validate_checkpoint({
        "type": "visual",
        "description": "The header is dark blue.",
        "review_status": "needs_review",
        "review_reason": "No exact colour is stated.",
    })
    assert cp["review_status"] == "needs_review"
    assert cp["review_reason"] == "No exact colour is stated."


# ── The flag survives into the text the agent actually reads ─────────────────

def test_the_flag_is_rendered_into_the_skill_goal_text():
    """AISkill.review_status is a database column; the rendered goal is what
    travels into the Vibe goal box and exports. A warning that only lives in
    a column is one the person running the skill never sees."""
    md = render_skill_markdown(
        role=None, objective="Do the thing.", context=None,
        instructions=["Step one."], notes=[],
        review_status="needs_review", review_reason="No pass condition stated.",
    )
    assert md.startswith("# ⚠️ Needs Review")
    assert "not ready to run as-is" in md
    assert "No pass condition stated." in md


def test_needs_design_flow_renders_its_own_label():
    md = render_skill_markdown(
        role=None, objective="x", context=None, instructions=["y"], notes=[],
        review_status="needs_design_flow", review_reason=None,
    )
    assert "# ⚠️ Needs Design Flow" in md


def test_a_ready_skill_has_no_banner():
    md = render_skill_markdown(
        role=None, objective="x", context=None, instructions=["y"], notes=[],
    )
    assert "Needs Review" not in md
    assert md.startswith("# Objective")


def test_a_flagged_checkpoint_still_has_a_description_for_the_api_filter():
    """visual_audit.py filters checkpoints on a truthy description. A flagged
    checkpoint must pass that filter or the flag would be invisible."""
    cp = _validate_checkpoint(_functional(objective="The row saves."))
    assert cp["description"]
    assert "Needs Review" in cp["description"]
