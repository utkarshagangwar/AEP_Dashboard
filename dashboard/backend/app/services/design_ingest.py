"""The Brain — design/SOW ingestion (Phase 3).

Turns an uploaded SOW document (txt / md / pdf) into structured visual
checkpoints and functional skills via the LLM router, and caches the result
in the Memory Bank (design_rules) keyed by the artifact's sha256 so each
document is ever parsed once.

Large documents are split into parts instead of being truncated, so every
part of the document is eventually analyzed rather than silently dropped.
Splitting is now structure-aware (app.services.doc_chunking, via the block
extraction in app.services.doc_blocks): parts break on headings, never
mid-table or mid-clause, and each carries the section path it came from.
chunk_text() below is the superseded character-window splitter, kept only
as the reference implementation for the unstructured-input fallback.

Checkpoint schema stored in design_rules.checkpoints (JSONB):
  [
    {
      "type": "functional" | "visual",
      "title": str,             # short label, e.g. "Job Creation"
      "description": str,       # visual: a short testable claim about
                                 # appearance. functional: a deterministically
                                 # rendered Role/Objective/Context/Instructions/
                                 # Notes markdown "skill" built from the
                                 # structured fields below — never written
                                 # freehand by the LLM, so formatting is
                                 # always consistent. This is what actually
                                 # becomes the AI agent's goal text.
      "role": str | null,       # functional only: persona/preconditions
      "objective": str | null,  # functional only: one-sentence pass criterion
      "context": str | null,    # functional only: starting page/state
      "instructions": [str],    # functional only: ordered, atomic steps
      "notes": [str],           # functional only: caveats/expected values
      "page": str | null,       # page/screen it applies to, if stated
      "expected": str | null    # explicit expected value/behavior, if stated
    },
    ...
  ]

Every functional checkpoint is saved directly as a skill (see
app.services.skill_store) as soon as its part finishes parsing — no live
browser run is required to produce it.

Reliability rules:
  * Text extraction failures and LLM failures surface as parse_status="error"
    with a human-readable parse_error — never a silent empty result.
  * LLM output is schema-validated entry by entry; invalid entries are
    dropped, never repaired by guessing.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger
from app.services import doc_blocks as _doc_blocks

logger = get_logger(__name__)

_MAX_SOW_CHARS = 2_000_000  # sanity ceiling only — guards against pathological input
_CHUNK_MAX_CHARS = 20_000  # ~5k tokens per part — keeps each LLM call well within budget

_SOW_SYSTEM = (
    "You are a senior QA analyst turning a Statement of Work / requirements "
    "document for a web application into QA checkpoints. Respond with JSON "
    "only:\n"
    '{"checkpoints": [{"type": "functional"|"visual", "title": str, '
    '"description": str|null, "role": str|null, "objective": str|null, '
    '"context": str|null, "instructions": [str]|null, "notes": [str]|null, '
    '"page": str|null, "expected": str|null, "review_status": "ready"|'
    '"needs_review"|"needs_design_flow", "review_reason": str|null}]}\n'
    "\n"
    "For type \"visual\" (layout/branding/design requirements): fill only "
    "'description' — a short, testable claim about appearance. Leave role/"
    "objective/context/instructions/notes null.\n"
    "\n"
    "For type \"functional\" (behavior/workflow requirements): leave "
    "'description' null and instead fill these structured fields — a "
    "browser-automation AI agent will execute this skill with no extra "
    "context, so be concrete and complete:\n"
    "  'role': the persona/preconditions for this test, e.g. \"Logged in as "
    "an admin user with permission to create jobs.\"\n"
    "  'objective': one sentence defining what a PASS looks like, e.g. "
    "\"A new job is created and appears in the job list.\"\n"
    "  'context': the starting page/state, e.g. \"Start from the Jobs list "
    "page.\"\n"
    "  'instructions': an ORDERED array of atomic, imperative steps (one "
    "action per string), e.g. [\"Click the 'Create Job' button.\", \"Enter "
    "'QA Engineer' into the Job Title field.\", \"Click Submit.\", \"Verify "
    "the new job appears at the top of the list.\"]. Spell out which fields "
    "to fill (with plausible example values if the document doesn't give "
    "exact ones) and which controls to interact with. Make implied steps "
    "explicit — e.g. if the document says 'user can add candidates', spell "
    "out opening the Add Candidate form, filling each required field, and "
    "submitting it — but do NOT invent requirements that aren't implied by "
    "the document.\n"
    "  'notes': an array of caveats, edge cases, or expected values worth "
    "flagging (can be empty).\n"
    "\n"
    "'title' is a short (3-6 word) label for the functionality, e.g. "
    "\"Job Creation\", \"Add Candidate\" — used to identify this skill "
    "across re-analysis, so keep it stable and specific.\n"
    "\n"
    "ONE CHECKPOINT PER FEATURE. Organize by feature, not by paragraph: a "
    "feature with several distinct user flows (create / edit / delete, or "
    "search / filter / sort) produces a SEPARATE checkpoint per flow, each "
    "independently executable, rather than one broad checkpoint that tries "
    "to cover them all. A checkpoint a tester cannot run start-to-finish on "
    "its own is too broad — split it.\n"
    "\n"
    "COMPLETENESS VS HONESTY — 'review_status'. Some requirements are stated "
    "but under-specified or conflicting: the document says a control exists "
    "without saying what it does, names a screen without describing how to "
    "reach it, or gives two incompatible behaviours for the same feature. "
    "For those:\n"
    "  - Still emit the checkpoint. Never omit a requirement because it was "
    "vague — an omitted requirement is invisible to everyone downstream and "
    "will never be clarified or tested.\n"
    "  - Never invent steps to make it look complete. Fabricated steps are "
    "worse than acknowledged gaps: they produce tests that pass against "
    "behaviour nobody specified.\n"
    "  - Set 'review_status' to \"needs_review\" and put in 'review_reason' "
    "exactly what is missing or conflicting (e.g. \"the document lists a "
    "Status dropdown but never states its allowed values\" or \"two sections "
    "disagree on whether deletion requires confirmation\").\n"
    "  - Use \"needs_design_flow\" instead when the requirement implies a "
    "user flow the document never describes at all — it names a screen, "
    "outcome or state but no path to reach it. That needs a design "
    "decision, not just a clarification.\n"
    "  - Use \"ready\" ONLY when the steps you wrote are fully grounded in "
    "what the document actually states. If you had to guess at any step, it "
    "is not ready.\n"
    "\n"
    'Return {"checkpoints": []} if no testable requirements are found.'
)


# IngestError moved to app.services.doc_blocks (SOW_CHUNKING_PLAN Phase 1) so
# that module has no import dependency on this one -- design_ingest imports
# doc_blocks, not the reverse. Re-exported here as the SAME class object, so
# every existing `from app.services.design_ingest import IngestError` and
# `except design_ingest.IngestError` keeps working with no change.
IngestError = _doc_blocks.IngestError


# ATG's metered Gemini Flash gateway (see llm_router._resolve_call_target).
# Brain-only: scoped here via model_override rather than as the router's
# global VISUAL_LLM_PRIMARY, so it doesn't silently change behavior for the
# other llm_router.complete() callers (SOW Creation/Rewrite, orchestrator,
# visual_judge).
_AXON_MODEL = "axon/gemini-flash-latest"


def _complete_via_brain(prompt: str, *, system: str, max_tokens: int):
    """Run a Brain completion, preferring AXON and falling back to the
    router's default chain (native Gemini/Google/OpenRouter keys) if AXON is
    unset or its $10 metered budget is exhausted (gateway returns HTTP 402).
    Keeps SOW ingestion working without AXON being a hard dependency."""
    from app.services import llm_router

    if os.environ.get("AXON_API_KEY", "").strip():
        try:
            return llm_router.complete(
                prompt,
                system=system,
                expect_json=True,
                max_tokens=max_tokens,
                model_override=_AXON_MODEL,
            )
        except llm_router.LLMRouterError as exc:
            logger.warning(
                "Brain: AXON call failed (%s), falling back to default LLM chain", exc
            )

    return llm_router.complete(
        prompt,
        system=system,
        expect_json=True,
        max_tokens=max_tokens,
    )


# ── Text extraction ──────────────────────────────────────────────────────────

_SUPPORTED_EXTENSIONS = (".txt", ".md", ".pdf")


def extract_blocks(storage_path: str, file_name: str) -> list[_doc_blocks.Block]:
    """Structured blocks for a SOW file (SOW_CHUNKING_PLAN Phase 1).

    This is what app.services.doc_chunking consumes -- headings, tables and
    page markers preserved, so chunks can split on real section boundaries
    and carry a "p.12" locator.

    The extension gate is applied HERE rather than delegated wholesale to
    doc_blocks.extract_blocks, which also accepts .docx. This pipeline (SOW
    Checkpoints / Video Walkthrough) has only ever accepted txt/md/pdf, and
    silently widening it would be an unrelated behavior change to a shipped
    endpoint -- .docx support belongs to app.services.sow_import.
    """
    ext = os.path.splitext(file_name.lower())[1]
    if ext not in _SUPPORTED_EXTENSIONS:
        raise IngestError(f"Unsupported SOW format '{ext}'. Use .txt, .md, or .pdf.")
    return _doc_blocks.extract_blocks(storage_path, file_name)


def extract_text(storage_path: str, file_name: str) -> str:
    """Extract plain text from a SOW file. Supports .txt, .md, .pdf.

    UNCHANGED by SOW_CHUNKING_PLAN Phase 1, deliberately. An earlier draft
    routed this through doc_blocks.render_blocks() for a single source of
    truth; that was reverted because rendering is lossy for this caller --
    it strips markdown heading markers ("## 4.3 Scope" -> "4.3 Scope") and
    list bullets, so the SOW Checkpoints LLM prompt would have lost the
    document structure it can currently see in the raw text. A cleanliness
    refactor is not worth degrading a shipped prompt.

    Structure-aware consumers use extract_blocks() above instead. The two
    functions read the same files by different routes on purpose: this one
    preserves the author's original bytes, that one preserves the author's
    semantics.
    """
    ext = os.path.splitext(file_name.lower())[1]

    if ext in (".txt", ".md"):
        try:
            with open(storage_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            raise IngestError(f"Could not read document: {exc}") from exc

    elif ext == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(storage_path)
            if reader.is_encrypted:
                raise IngestError("PDF is password-protected; upload an unlocked copy.")
            pages = [(page.extract_text() or "") for page in reader.pages]
            text = "\n\n".join(pages)
        except IngestError:
            raise
        except Exception as exc:  # noqa: BLE001 — pypdf raises many exception types
            raise IngestError(f"Could not parse PDF: {exc}") from exc

    else:
        raise IngestError(f"Unsupported SOW format '{ext}'. Use .txt, .md, or .pdf.")

    text = text.strip()
    if not text:
        raise IngestError(
            "No text could be extracted. If this is a scanned PDF, it needs OCR first."
        )
    if len(text) > _MAX_SOW_CHARS:
        # Not a normal document size — refuse rather than silently drop content.
        raise IngestError(
            f"Document is too large to ingest ({len(text):,} chars). "
            "Split it into smaller files and upload separately."
        )
    return text


# ── Chunking for large documents ─────────────────────────────────────────────

def chunk_text(text: str, max_chars: int = _CHUNK_MAX_CHARS) -> list[str]:
    """DEPRECATED — use app.services.doc_chunking.chunk_document instead.

    Superseded by SOW_CHUNKING_PLAN Phase 2. This splitter is structure-
    blind: it breaks on "\\n\\n" and a character count, so it cuts through
    tables and numbered clauses, and the parts it returns carry no section
    context. Both defects are why a large SOW's ledger loses requirements at
    chunk boundaries.

    Kept, unmodified, for two reasons:
      1. It is the reference implementation for the paragraph fallback --
         tests/test_doc_chunking.py (T-C-015) asserts chunk_document()
         reproduces its output byte-for-byte for unstructured input, so a
         document with no structure to exploit chunks exactly as it does
         today.
      2. Any caller not yet migrated keeps working unchanged.

    No production code path calls this any more.

    ---

    Split text into parts of at most ~max_chars, breaking on paragraphs.

    Every character of the input is preserved across the returned parts (no
    content is dropped). A document that already fits in one part is returned
    as a single-element list, unchanged — this is the path small SOWs take, so
    their behavior is unaffected by chunking.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        if current:
            parts.append("\n\n".join(current))

    for para in paragraphs:
        para_len = len(para)
        # A single paragraph larger than max_chars can't be accumulated with
        # anything else — flush what we have, then hard-split it on its own.
        if para_len > max_chars:
            flush()
            current, current_len = [], 0
            for i in range(0, para_len, max_chars):
                parts.append(para[i : i + max_chars])
            continue

        added_len = para_len + (2 if current else 0)  # account for the "\n\n" join
        if current and current_len + added_len > max_chars:
            flush()
            current, current_len = [para], para_len
        else:
            current.append(para)
            current_len += added_len

    flush()
    return parts or [text]


# ── Checkpoint extraction via the router ─────────────────────────────────────

_MAX_INSTRUCTIONS = 25
_MAX_NOTES = 15
_MAX_ROLE_CHARS = 300
_MAX_OBJECTIVE_CHARS = 400
_MAX_CONTEXT_CHARS = 500
_MAX_INSTRUCTION_CHARS = 500
_MAX_NOTE_CHARS = 300
_MAX_SKILL_CHARS = 6000


def _clean_str_list(value: object, *, max_items: int, max_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            cleaned.append(text[:max_chars])
        if len(cleaned) >= max_items:
            break
    return cleaned


def render_skill_markdown(
    *,
    role: str | None,
    objective: str,
    context: str | None,
    instructions: list[str],
    notes: list[str],
    review_status: str | None = None,
    review_reason: str | None = None,
) -> str:
    """Deterministic Role/Objective/Context/Instructions/Notes markdown.

    The LLM only ever emits the structured fields (never raw markdown), so
    this is the single place that decides formatting — every skill looks the
    same regardless of model quirks. This rendered text is what actually
    becomes the AI agent's goal (AISkill.goal / "Use as goal").

    A flagged skill leads with a "Needs review" banner. That is deliberate
    duplication of AISkill.review_status: the rendered text travels places
    the column does not (it IS the agent's goal, it gets copied into the
    Vibe goal box, it appears in exports), and a warning that only lives in
    a database column is a warning the person actually running the skill
    never sees.
    """
    sections: list[str] = []
    if review_status:
        label = (
            "Needs Design Flow"
            if review_status == "needs_design_flow"
            else "Needs Review"
        )
        banner = (
            f"# ⚠️ {label}\n"
            "This skill is NOT fully specified by the source document and is not "
            "ready to run as-is."
        )
        if review_reason:
            banner += f"\n\nWhat is missing: {review_reason}"
        sections.append(banner)
    if role:
        sections.append(f"# Role\n{role}")
    sections.append(f"# Objective\n{objective}")
    if context:
        sections.append(f"# Context\n{context}")
    instructions_body = "\n\n".join(
        f"## Instruction {i}\n{step}" for i, step in enumerate(instructions, start=1)
    )
    sections.append(f"# Instructions\n{instructions_body}")
    if notes:
        notes_body = "\n".join(f"- {note}" for note in notes)
        sections.append(f"# Notes\n{notes_body}")
    return "\n\n".join(sections)[:_MAX_SKILL_CHARS]


_VALID_REVIEW_STATUSES = ("needs_review", "needs_design_flow")
_UNSPECIFIED_STEP = (
    "The source document does not specify the steps for this requirement. "
    "Clarify what this control/flow actually does before running this skill."
)


def _read_review_status(item: dict) -> tuple[str | None, str | None]:
    """(review_status, review_reason) as supplied by the model.

    Anything other than the two flagged values — including the literal
    "ready", an unknown string, or a missing key — normalises to None,
    meaning "runnable as written". Unknown values are treated as ready
    rather than flagged so a model quirk cannot mark an entire document as
    needing review; the validator below flags on the concrete evidence
    (missing steps) independently of what the model claimed.
    """
    raw = item.get("review_status")
    status = raw if raw in _VALID_REVIEW_STATUSES else None
    reason = str(item.get("review_reason") or "").strip()[:2000] or None
    return status, (reason if status else None)


def _validate_checkpoint(item: object) -> dict | None:
    """Return a normalized checkpoint dict, or None if there is genuinely
    nothing to record.

    An under-specified requirement is FLAGGED (review_status), never
    dropped. Dropping was the previous behaviour and it was the worst
    available option: the requirement disappeared from the checkpoint list,
    from the skills list, and from every count the UI showed, so a document
    full of vague requirements looked identically "fully parsed" to one that
    was actually complete. Only a checkpoint with no usable content at all
    still returns None.
    """
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    ctype = item.get("type")
    if ctype not in ("functional", "visual"):
        return None
    page = (str(item["page"]).strip()[:500] or None) if item.get("page") else None
    expected = (
        (str(item["expected"]).strip()[:2000] or None) if item.get("expected") else None
    )
    review_status, review_reason = _read_review_status(item)

    if ctype == "visual":
        description = str(item.get("description") or "").strip()
        if not description:
            return None
        return {
            "type": "visual",
            "title": (title or description[:80])[:200],
            "description": description[:2000],
            "role": None,
            "objective": None,
            "context": None,
            "instructions": [],
            "notes": [],
            "page": page,
            "expected": expected,
            "review_status": review_status,
            "review_reason": review_reason,
        }

    # functional — prefer the structured fields; fall back to a single-step
    # skill built from a legacy/non-compliant flat 'description' so a model
    # that ignores the new schema still produces something usable rather
    # than being dropped outright.
    role = str(item.get("role") or "").strip()[:_MAX_ROLE_CHARS] or None
    objective = str(item.get("objective") or "").strip()[:_MAX_OBJECTIVE_CHARS]
    context = str(item.get("context") or "").strip()[:_MAX_CONTEXT_CHARS] or None
    instructions = _clean_str_list(
        item.get("instructions"), max_items=_MAX_INSTRUCTIONS, max_chars=_MAX_INSTRUCTION_CHARS
    )
    notes = _clean_str_list(item.get("notes"), max_items=_MAX_NOTES, max_chars=_MAX_NOTE_CHARS)

    legacy_description = str(item.get("description") or "").strip()
    if not instructions and legacy_description:
        instructions = [legacy_description[:_MAX_INSTRUCTION_CHARS]]
    if not objective:
        objective = (legacy_description[:_MAX_OBJECTIVE_CHARS] or title)[:_MAX_OBJECTIVE_CHARS]

    # Nothing at all to record — no title, no objective, no description.
    # This is the only case that is still dropped.
    if not objective and not title:
        return None

    # Missing steps or objective means the source document didn't specify
    # this requirement well enough to execute it. Flag it and keep it: the
    # requirement is real, it just isn't runnable yet. The flag is derived
    # from the evidence here rather than trusted from the model, so a model
    # that claims "ready" while omitting the steps is still caught.
    missing: list[str] = []
    if not instructions:
        missing.append("no executable steps are given")
    if not objective:
        missing.append("no pass condition is stated")

    if missing:
        review_status = review_status or "needs_review"
        review_reason = review_reason or (
            "The source document describes this requirement but "
            + " and ".join(missing)
            + "."
        )
        if not instructions:
            instructions = [_UNSPECIFIED_STEP]
        if not objective:
            objective = (title or instructions[0])[:_MAX_OBJECTIVE_CHARS]

    description = render_skill_markdown(
        role=role,
        objective=objective,
        context=context,
        instructions=instructions,
        notes=notes,
        review_status=review_status,
        review_reason=review_reason,
    )
    return {
        "type": "functional",
        "title": (title or objective[:80])[:200],
        "description": description,
        "role": role,
        "objective": objective,
        "context": context,
        "instructions": instructions,
        "notes": notes,
        "page": page,
        "expected": expected,
        "review_status": review_status,
        "review_reason": review_reason,
    }


def parse_sow(text: str, *, part_label: str | None = None) -> tuple[list[dict], str]:
    """Extract checkpoints from SOW text. Returns (checkpoints, model_used).

    part_label (e.g. "part 2 of 4"): when set, the prompt notes that this is
    only an excerpt of a larger document so the model doesn't assume missing
    context means the requirement doesn't exist.

    Raises IngestError on total provider failure (caller marks the artifact
    as errored; a later retry can re-enqueue).
    """
    from app.services import llm_router

    prompt = "Extract QA checkpoints from this document"
    if part_label:
        prompt += (
            f" ({part_label} of a larger document — this is an excerpt; "
            "analyze only what appears in this excerpt)"
        )
    prompt += ":\n\n" + text

    try:
        result = _complete_via_brain(prompt, system=_SOW_SYSTEM, max_tokens=8192)
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    raw = result.parsed_json or {}
    items = raw.get("checkpoints", []) if isinstance(raw, dict) else []
    checkpoints = [c for c in (_validate_checkpoint(i) for i in items) if c]

    dropped = len(items) - len(checkpoints)
    if dropped:
        logger.warning("SOW ingest: dropped %d schema-invalid checkpoint(s)", dropped)
    logger.info(
        "SOW ingest: %d checkpoint(s) extracted via %s", len(checkpoints), result.model_used
    )
    return checkpoints, result.model_used
