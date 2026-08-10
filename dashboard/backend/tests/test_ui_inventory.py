"""Unit tests for app.services.ui_inventory — the UI naming reference.

Scope: the deterministic parts — normalisation of the vision reply, the
rendered text, the prompt framing, and the staleness key. No database, no
network, no images.

WHAT THIS PASS IS FOR. Extraction only ever saw the requirements document, so
a checkpoint said "click Submit Application" because that is what the document
called it, while the product's button reads "Apply Now". The test then failed
for a reason that was neither a product defect nor a spec gap. This module
supplies the real names.

THE RULE IT MUST NOT BREAK. The inventory is vocabulary, never requirements. A
button visible in a screenshot is not evidence that anyone asked for it to be
tested, and an inventory that could add behaviours would reintroduce the
"everything becomes a TDD" defect from the opposite direction.
"""
from __future__ import annotations

from app.services import ui_inventory as ui


def _screen(**over):
    base = {
        "screen": "Jobs list",
        "controls": ["Create Job", "Archive"],
        "fields": ["Job Title"],
        "nav": ["Jobs", "Candidates"],
        "messages": [],
    }
    base.update(over)
    return base


# ── Normalising the model's reply ────────────────────────────────────────────

def test_normalize_keeps_exact_label_text():
    """Transcription, not paraphrase: the exact string is the entire point."""
    out = ui.normalize_inventory({"screens": [_screen(controls=["Apply Now"])]})
    assert out[0]["controls"] == ["Apply Now"]


def test_normalize_drops_screens_with_no_labels():
    """A screen name carrying no vocabulary is not worth prompt budget — the
    only thing this pass produces is labels."""
    out = ui.normalize_inventory({
        "screens": [
            {"screen": "Empty", "controls": [], "fields": [], "nav": [], "messages": []},
            _screen(),
        ]
    })
    assert [s["screen"] for s in out] == ["Jobs list"]


def test_normalize_dedupes_case_insensitively_and_keeps_order():
    out = ui.normalize_inventory({
        "screens": [_screen(controls=["Create Job", "create job", "Archive"])]
    })
    assert out[0]["controls"] == ["Create Job", "Archive"]


def test_normalize_survives_junk():
    """A malformed reply must degrade to 'no inventory', never raise — the
    caller's response to no inventory is the same as to a failed call."""
    assert ui.normalize_inventory({}) == []
    assert ui.normalize_inventory({"screens": "not a list"}) == []
    assert ui.normalize_inventory({"screens": [None, 3, "x"]}) == []
    assert ui.normalize_inventory(None) == []


def test_normalize_bounds_runaway_output():
    huge = {"screens": [_screen(controls=[f"Button {i}" for i in range(500)])] * 200}
    out = ui.normalize_inventory(huge)
    assert len(out) <= ui._MAX_SCREENS
    assert len(out[0]["controls"]) <= ui._MAX_ITEMS_PER_SCREEN


# ── Rendering ────────────────────────────────────────────────────────────────

def test_render_is_empty_for_no_screens():
    """Empty string, not a header with nothing under it: format_for_prompt
    keys off falsiness to decide whether to inject anything at all."""
    assert ui.render_inventory([]) == ""


def test_render_lists_every_label_group():
    text = ui.render_inventory([_screen(messages=["No jobs yet"])])
    assert "Jobs list" in text
    assert "Create Job" in text
    assert "Job Title" in text
    assert "Candidates" in text
    assert "No jobs yet" in text


def test_render_omits_groups_with_nothing_in_them():
    text = ui.render_inventory([_screen(messages=[], nav=[])])
    assert "messages:" not in text
    assert "nav:" not in text


# ── The prompt framing ───────────────────────────────────────────────────────

def test_format_injects_nothing_without_an_inventory():
    assert ui.format_for_prompt(None) == ""
    assert ui.format_for_prompt("") == ""


def test_format_states_the_vocabulary_not_requirements_rule():
    """Without this rule at the point of use, a list of buttons reads as a
    list of features and the extractor writes checkpoints nobody asked for."""
    text = ui.format_for_prompt(ui.render_inventory([_screen()]))
    assert "VOCABULARY, NOT REQUIREMENTS" in text
    assert "not evidence that anyone asked for it to be tested" in text


def test_format_tells_the_model_what_to_do_when_a_control_is_missing():
    """The reference is partial by nature. Silence here invites the model to
    either invent a name or conclude the control does not exist."""
    text = ui.format_for_prompt(ui.render_inventory([_screen()]))
    assert "use the document's wording" in text
    assert "partial by nature" in text


def test_format_carries_the_labels_themselves():
    text = ui.format_for_prompt(ui.render_inventory([_screen(controls=["Apply Now"])]))
    assert "Apply Now" in text


# ── Staleness key ────────────────────────────────────────────────────────────

def test_source_key_is_order_independent_and_deduped():
    """Two artifacts added in a different order are the same evidence set —
    otherwise every ingest would rebuild and pay for a vision call."""
    assert ui._source_key(["b", "a"]) == ui._source_key(["a", "b", "a"])


def test_source_key_changes_when_evidence_is_added():
    """The whole answer to 'we uploaded screenshots after the first SOW':
    a changed set is what triggers the rebuild."""
    assert ui._source_key(["a"]) != ui._source_key(["a", "b"])


def test_build_stamps_evidence_that_EXISTS_not_evidence_it_used(monkeypatch):
    """Regression: keying staleness on what the build USED means the stored
    set is permanently unequal to the current set whenever a screenshot was
    skipped (oversized, over the image cap, or a video still digesting) — so
    every part of every SOW would rebuild and pay for another vision call.

    Stamp what existed; the cap is a build detail, not a change of evidence.
    """
    class _Row:
        project_id = "p1"
        source_artifact_ids = None
        inventory_json = None
        rendered_text = None
        screen_count = 0
        built_by_model = None
        build_error = None

    row = _Row()

    class _Query:
        def filter(self, *a, **k):
            return self

        def one_or_none(self):
            return row

    class _Session:
        def query(self, *a, **k):
            return _Query()

        def add(self, obj):
            pass

    # Three artifacts exist; the build can use none of them (all skipped).
    monkeypatch.setattr(ui, "_current_source_ids", lambda db, pid: ["a", "b", "c"])
    monkeypatch.setattr(ui, "_evidence_artifacts", lambda db, pid: [])
    monkeypatch.setattr(ui, "_video_label_hints", lambda db, pid: ("", []))
    monkeypatch.setattr(ui, "_load_images", lambda arts: ([], []))

    built = ui.build_inventory(_Session(), "p1")
    assert built.source_artifact_ids == ["a", "b", "c"], (
        "must stamp existing evidence, or the next call rebuilds again"
    )


# ── Config ───────────────────────────────────────────────────────────────────

def test_inventory_is_on_by_default(monkeypatch):
    monkeypatch.delenv("TDD_UI_INVENTORY", raising=False)
    assert ui.inventory_enabled()


def test_inventory_can_be_disabled(monkeypatch):
    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("TDD_UI_INVENTORY", value)
        assert not ui.inventory_enabled(), value


# ── Wiring into extraction ───────────────────────────────────────────────────

def test_extraction_prompt_carries_the_inventory():
    from app.services.tdd_extraction import build_extraction_system

    rendered = ui.render_inventory([_screen(controls=["Apply Now"])])
    with_inventory = build_extraction_system(None, rendered)
    without = build_extraction_system(None)

    assert "Apply Now" in with_inventory
    assert "Apply Now" not in without
    # The category contract must survive the addition — the naming reference
    # is appended to the rules, it does not replace them.
    assert "CATEGORY CONTRACT" in with_inventory


def test_extraction_prompt_is_unchanged_without_an_inventory():
    """A project with no evidence must extract exactly as it did before this
    existed — no empty header, no dangling instructions about a reference
    that isn't there."""
    from app.services.tdd_extraction import build_extraction_system

    assert build_extraction_system(None, None) == build_extraction_system(None)
    assert build_extraction_system(None, "") == build_extraction_system(None)
