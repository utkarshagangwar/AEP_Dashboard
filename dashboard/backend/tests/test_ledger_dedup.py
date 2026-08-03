"""Phase 4 — cross-chunk fact deduplication (T-D-001..008).

SOW_CHUNKING_PLAN.md §3 Phase 4.
"""
from __future__ import annotations

import pytest

from app.services.ledger_dedup import dedupe_facts, normalize_label


def fact(label, *, fact_type="ui_element", element_type="button", location=None,
         notes=None, source_ref=None):
    return {
        "fact_type": fact_type,
        "element_type": element_type,
        "label": label,
        "location": location,
        "behavior_notes": notes,
        "source_ref": source_ref,
    }


# ── T-D-001 — basic merging ──────────────────────────────────────────────────

def test_td001_identical_facts_across_chunks_merge_to_one():
    facts, merged = dedupe_facts([
        fact("Bulk delete", source_ref="§2.1"),
        fact("Bulk delete", source_ref="§4.3"),
    ])
    assert len(facts) == 1
    assert merged == 1
    assert facts[0]["source_ref"] == "§2.1; §4.3"


def test_td001_distinct_facts_are_untouched():
    facts, merged = dedupe_facts([
        fact("Bulk delete"),
        fact("Status filter", element_type="dropdown"),
        fact("Export", element_type="button"),
    ])
    assert len(facts) == 3
    assert merged == 0


# ── T-D-002 — label normalisation ────────────────────────────────────────────

@pytest.mark.parametrize("variant", [
    "Delete",
    "delete",
    "DELETE",
    "  Delete  ",
    "the Delete",
    "Delete button",
    "The Delete Button",
    '"Delete"',
    "Delete,",
])
def test_td002_label_variants_collapse(variant):
    facts, merged = dedupe_facts([fact("Delete"), fact(variant)])
    assert len(facts) == 1, f"{variant!r} did not collapse onto 'Delete'"
    assert merged == 1


def test_td002_longest_surface_form_survives():
    facts, _ = dedupe_facts([fact("Delete"), fact("Delete selected candidates button")])
    # Not a collapse -- different labels after normalisation.
    assert len(facts) == 2

    facts, _ = dedupe_facts([fact("Delete"), fact("the Delete button")])
    assert len(facts) == 1
    assert facts[0]["label"] == "the Delete button"


def test_td002_role_noun_only_label_is_not_reduced_to_empty():
    """A label that is ONLY its role noun must keep an identifying key --
    an empty key would swallow every other button fact."""
    assert normalize_label("button", "button") == "button"
    facts, _ = dedupe_facts([fact("button"), fact("Export")])
    assert len(facts) == 2


# ── T-D-003 — element_type is part of the key ────────────────────────────────

def test_td003_same_label_different_element_type_stays_separate():
    """The guard against false merges: a 'Filter' dropdown and a 'Filter'
    button are two controls."""
    facts, merged = dedupe_facts([
        fact("Filter", element_type="dropdown"),
        fact("Filter", element_type="button"),
    ])
    assert len(facts) == 2
    assert merged == 0


def test_td003_same_label_different_fact_type_stays_separate():
    facts, _ = dedupe_facts([
        fact("Bulk delete", fact_type="feature", element_type=None),
        fact("Bulk delete", fact_type="ui_element", element_type="button"),
    ])
    assert len(facts) == 2


def test_td003_role_noun_not_stripped_for_mismatched_element_type():
    """'Delete dropdown' typed as a button must not lose the word 'dropdown'
    and collide with 'Delete'."""
    assert normalize_label("Delete dropdown", "button") == "delete dropdown"


# ── T-D-004 — locations are unioned ──────────────────────────────────────────

def test_td004_differing_locations_are_both_kept():
    facts, _ = dedupe_facts([
        fact("Export", location="Header"),
        fact("Export", location="Candidate list"),
    ])
    assert len(facts) == 1
    assert facts[0]["location"] == "Header; Candidate list"


def test_td004_duplicate_location_not_repeated():
    facts, _ = dedupe_facts([
        fact("Export", location="Header"),
        fact("Export", location="Header"),
    ])
    assert facts[0]["location"] == "Header"


def test_td004_null_location_filled_from_other_occurrence():
    facts, _ = dedupe_facts([fact("Export"), fact("Export", location="Header")])
    assert facts[0]["location"] == "Header"


# ── T-D-005 — behaviour notes ────────────────────────────────────────────────

def test_td005_longest_notes_kept_and_unique_sentences_appended():
    facts, _ = dedupe_facts([
        fact("Delete", notes="Opens a confirmation modal before deleting."),
        fact("Delete", notes="Disabled when nothing is selected."),
    ])
    notes = facts[0]["behavior_notes"]
    assert "confirmation modal" in notes
    assert "Disabled when nothing is selected." in notes


def test_td005_identical_sentences_not_duplicated():
    text = "Opens a confirmation modal."
    facts, _ = dedupe_facts([fact("Delete", notes=text), fact("Delete", notes=text)])
    assert facts[0]["behavior_notes"] == text


def test_td005_notes_capped():
    long_a = "A" * 1800 + "."
    long_b = "B" * 1800 + "."
    facts, _ = dedupe_facts([fact("Delete", notes=long_a), fact("Delete", notes=long_b)])
    assert len(facts[0]["behavior_notes"]) <= 2000


# ── T-D-006 — source_ref joining and truncation ──────────────────────────────

def test_td006_source_refs_capped_at_three():
    facts, _ = dedupe_facts([fact("Delete", source_ref=f"ref-{i}") for i in range(6)])
    assert facts[0]["source_ref"].count(";") == 2
    assert facts[0]["source_ref"].startswith("ref-0")


def test_td006_source_ref_respects_column_limit():
    facts, _ = dedupe_facts([fact("Delete", source_ref="x" * 400) for _ in range(3)])
    assert len(facts[0]["source_ref"]) <= 500


def test_td006_duplicate_refs_not_repeated():
    facts, _ = dedupe_facts([fact("Delete", source_ref="§2.1")] * 3)
    assert facts[0]["source_ref"] == "§2.1"


# ── T-D-007 — order independence ─────────────────────────────────────────────

def test_td007_dedup_is_order_independent():
    a = [fact("Delete", location="Header"), fact("Export", location="Footer")]
    b = [fact("Export", location="Footer"), fact("Delete", location="Header")]

    def key_set(facts):
        return {(f["label"].casefold(), f["location"]) for f in facts}

    assert key_set(dedupe_facts(a + b)[0]) == key_set(dedupe_facts(b + a)[0])


def test_td007_output_preserves_first_seen_order():
    facts, _ = dedupe_facts([fact("Alpha"), fact("Beta"), fact("Alpha"), fact("Gamma")])
    assert [f["label"] for f in facts] == ["Alpha", "Beta", "Gamma"]


# ── T-D-008 — counting and robustness ────────────────────────────────────────

def test_td008_merged_count_returned(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        facts, merged = dedupe_facts([fact("Delete")] * 4 + [fact("Export")])
    assert merged == 3
    assert len(facts) == 2
    assert any("merged 3 duplicate" in r.message for r in caplog.records)


def test_td008_empty_input_returns_empty():
    assert dedupe_facts([]) == ([], 0)


def test_td008_malformed_entries_skipped_not_crashed():
    facts, _ = dedupe_facts([fact("Delete"), {"label": ""}, None, "junk"])  # type: ignore[list-item]
    assert len(facts) == 1


def test_td008_does_not_mutate_input():
    original = fact("Delete", location="Header")
    snapshot = dict(original)
    dedupe_facts([original, fact("Delete", location="Footer")])
    assert original == snapshot


def test_td008_non_ui_facts_dedupe_on_label_alone():
    facts, merged = dedupe_facts([
        fact("Sort defaults to newest first", fact_type="decision", element_type=None),
        fact("Sort defaults to newest first.", fact_type="decision", element_type=None),
    ])
    assert len(facts) == 1
    assert merged == 1
