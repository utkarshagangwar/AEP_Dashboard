"""SOW assembly (Phase 3 — "Pass 4", per SOW_FEATURE_PLAN.md §2).

Produces the framing sections around the functional sections
app.services.sow_drafting drafts from the requirements ledger, and defines
the standard SOW skeleton's ordering:

  1. Project Overview          — LLM-drafted, synthesized from feature/
  2. Scope of Work             — decision facts only (never every control).
  3..N. Functional sections    — one per group from sow_drafting, in the
                                  order the grouping pass returned them.
  N+1. Out of Scope            — templated placeholder, see below.
  N+2. Assumptions             — "
  N+3. Dependencies            — "
  N+4. Exclusions              — "
  N+5. Sign-off & Acceptance   — "

The five trailing sections are deliberately NEVER LLM-drafted. Contractual
terms (what's excluded, what's assumed, who signs off) are exactly the
kind of content this platform's own reliability rules forbid inventing —
"do not invent facts that weren't discussed" is meaningless for scope
boundaries and acceptance criteria a meeting transcript was never going to
state in the first place. These ship as explicit, clearly-marked
placeholders a human fills in, not a hallucinated first draft that looks
authoritative and isn't.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.design_ingest import IngestError
from app.services.sow_drafting import _slugify, _validate_block

logger = get_logger(__name__)

_OVERVIEW_BLOCK_SHAPE = (
    '[{"type": "heading", "level": int, "text": str}, '
    '{"type": "paragraph", "text": str}, '
    '{"type": "bullet_list", "items": [str, ...]}, '
    '{"type": "callout", "tone": "info"|"warning", "text": str}]'
)

_OVERVIEW_SYSTEM = (
    "You are a senior technical writer drafting the opening sections of a "
    "Statement of Work, from requirement facts gathered across meetings, "
    "recordings, and design references. Respond with JSON only:\n"
    '{"project_overview": ' + _OVERVIEW_BLOCK_SHAPE + ', '
    '"scope_of_work": ' + _OVERVIEW_BLOCK_SHAPE + "}\n\n"
    "project_overview: 1-2 short paragraph blocks describing what this "
    "project/product is, synthesized ONLY from the 'feature' and "
    "'decision' facts given to you — do not list individual UI controls "
    "here, that detail belongs in the functional sections that follow.\n\n"
    "scope_of_work: a single bullet_list block naming every distinct "
    "feature/area covered by the facts you were given — one bullet per "
    "feature area (matching the section groupings' granularity, not every "
    "individual control) — this is a table of contents for what follows, "
    "not a restatement of every detail.\n\n"
    "Base both sections ONLY on the facts given — do not invent business "
    "context, goals, or scope not evidenced in the facts. If the facts are "
    "too sparse to write a confident overview, say so plainly in a "
    "'callout' block (tone 'warning') instead of padding with generic "
    "language."
)


def draft_overview(document_title: str, facts: list) -> tuple[list[dict], list[dict], str]:
    """facts: every SowRequirementsLedger row for the document (all
    sections' worth — this pass sees the whole picture, unlike
    sow_drafting.draft_section which only sees one section's assigned
    facts). Returns (project_overview_blocks, scope_of_work_blocks,
    model_used). Raises IngestError only on total LLM failure — the caller
    decides how to degrade (see generate_sow_task: these two framing
    sections are marked 'error' individually, the rest of generation is
    unaffected)."""
    from app.services import llm_router
    from app.services.sow_drafting import _fact_summary

    relevant = [f for f in facts if (f.fact_type.value if hasattr(f.fact_type, "value") else str(f.fact_type)) in ("feature", "decision")]
    source = relevant or facts  # fall back to everything if nothing is tagged feature/decision
    indexed = [_fact_summary(f, i) for i, f in enumerate(source)]

    prompt = f"Document title: {document_title}\n\nRequirement facts:\n{indexed}"

    try:
        result = llm_router.complete(prompt, system=_OVERVIEW_SYSTEM, expect_json=True, max_tokens=4096)
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed while drafting the overview: {exc}") from exc

    raw = result.parsed_json or {}
    valid_indices = set(range(len(indexed)))

    overview_raw = raw.get("project_overview", []) if isinstance(raw, dict) else []
    scope_raw = raw.get("scope_of_work", []) if isinstance(raw, dict) else []
    overview_blocks = [b for b in (_validate_block(i, valid_indices) for i in overview_raw) if b]
    scope_blocks = [b for b in (_validate_block(i, valid_indices) for i in scope_raw) if b]

    if overview_blocks and overview_blocks[0].get("type") != "heading":
        overview_blocks.insert(0, {"type": "heading", "level": 2, "text": "Project Overview"})
    if not overview_blocks:
        overview_blocks = [{"type": "heading", "level": 2, "text": "Project Overview"}]

    if scope_blocks and scope_blocks[0].get("type") != "heading":
        scope_blocks.insert(0, {"type": "heading", "level": 2, "text": "Scope of Work"})
    if not scope_blocks:
        scope_blocks = [{"type": "heading", "level": 2, "text": "Scope of Work"}]

    logger.info("SOW assembly: drafted overview + scope of work via %s", result.model_used)
    return overview_blocks, scope_blocks, result.model_used


# ── Templated trailing sections — never LLM-drafted, see module docstring ──

_TEMPLATED_SPECS = [
    (
        "Out of Scope",
        "Not yet defined. List explicitly what is OUT of scope for this "
        "engagement — features, integrations, or platforms this SOW does "
        "not cover. Fill in before this document is treated as final.",
    ),
    (
        "Assumptions",
        "Not yet defined. Document the assumptions this SOW relies on — "
        "e.g. client-provided credentials/environments/content, existing "
        "infrastructure, timeline dependencies on other workstreams.",
    ),
    (
        "Dependencies",
        "Not yet defined. List external dependencies — third-party APIs, "
        "other teams' deliverables, infrastructure/tooling that must be in "
        "place before work can proceed.",
    ),
    (
        "Exclusions",
        "Not yet defined. List anything explicitly excluded from this "
        "engagement's deliverables (e.g. performance testing, "
        "localization, specific browser/device support).",
    ),
    (
        "Sign-off & Acceptance Criteria",
        "Not yet defined. Specify how completion will be verified and who "
        "signs off — e.g. acceptance test pass criteria, stakeholder "
        "approval process, delivery format.",
    ),
]


def build_templated_sections() -> list[dict]:
    """Static, non-LLM trailing sections — see module docstring for why
    these are never generated. Returns
    [{heading, section_key, content_blocks}, ...] in a fixed order."""
    used_keys: set[str] = set()
    sections = []
    for heading, note in _TEMPLATED_SPECS:
        sections.append({
            "heading": heading,
            "section_key": _slugify(heading) if _slugify(heading) not in used_keys else f"{_slugify(heading)}-2",
            "content_blocks": [
                {"type": "heading", "level": 2, "text": heading},
                {"type": "callout", "tone": "warning", "text": note},
            ],
        })
        used_keys.add(sections[-1]["section_key"])
    return sections
