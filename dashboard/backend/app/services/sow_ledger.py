"""SOW requirements-ledger extraction (Phase 1 — "Pass 1", simplified).

Turns one attached source (meeting transcript text, meeting recording
audio/video, or a design reference image) into rows of
app.models.sow.SowRequirementsLedger — the flat, exhaustive fact/UI-element
checklist a SOW is later drafted from and audited against (see
SOW_FEATURE_PLAN.md §2 Pass 1 and §11.6). This module deliberately runs the
opposite direction from app.services.design_ingest: that module turns an
uploaded SOW INTO checkpoints; this one turns meeting/design material INTO
the ledger a SOW will eventually be written FROM.

Phase 1 scope note: this is the "raw ledger dump" the plan's phased
delivery table (§8) calls for at this stage — real LLM extraction, real
validated output, but a single pass per source with no cross-source
grouping/regrouping or completeness audit yet (that formalization is
Phase 2's job, once this pipeline has been exercised against real
material). Nothing here is a stub: every path below produces genuine,
schema-validated ledger rows or a genuine IngestError.

Reliability rule, same as design_ingest/video_ingest: invalid LLM output is
dropped entry-by-entry with a logged count, never silently repaired by
guessing, and total failure surfaces as IngestError — never an empty
"success".
"""
from __future__ import annotations

import base64
import os
import time

from app.core.logging import get_logger
from app.services.design_ingest import IngestError
from app.services.doc_blocks import Block
from app.services.doc_chunking import Chunk, chunk_document
from app.services.ledger_dedup import dedupe_facts

logger = get_logger(__name__)

# Per-chunk retry policy (SOW_CHUNKING_PLAN Phase 4, defect D4). Transient
# provider failures are common enough that failing a whole source on the
# first one would make imports flaky; three attempts with backoff separates
# "the provider blipped" from "this chunk cannot be extracted".
_CHUNK_ATTEMPTS = 3
_CHUNK_BACKOFF_SECONDS = (1, 4)

# Output budget for one extraction call. Env-overridable because it trades
# directly against spend. 8192 (the previous hardcoded value) was the single
# biggest source of silent content loss on large documents: a dense chunk
# routinely produced more facts than fit, the response was cut mid-array, and
# llm_router's JSON repair pass turned the truncated array into a valid
# shorter one. See doc_chunking.MAX_CHARS_BY_DOC_KIND for the input side of
# the same budget.
_LEDGER_MAX_TOKENS = int(os.environ.get("SOW_LEDGER_MAX_TOKENS", "").strip() or 16384)

# How many times a truncated chunk may be halved and re-extracted before we
# give up and record the gap explicitly. Two levels turns one 8,000-char
# chunk into at most four 2,000-char ones, which is far below anything that
# can overflow the raised token budget; a third level would cost more calls
# than it could plausibly recover.
_MAX_SPLIT_DEPTH = 2

_VALID_FACT_TYPES = {"feature", "decision", "ui_element", "open_question"}
_VALID_ELEMENT_TYPES = {
    "button", "dropdown", "filter", "checkbox", "toggle", "slider",
    "three_dot_menu", "tab", "modal", "other",
}
_MAX_LABEL_CHARS = 500
_MAX_LOCATION_CHARS = 500
_MAX_NOTES_CHARS = 2000
_MAX_SOURCE_REF_CHARS = 300
_MAX_FACTS_PER_CALL = 400  # sanity ceiling — a single call returning more than
                            # this is almost certainly a degenerate/repeating
                            # response, not a genuinely huge screen. Raised
                            # from 200: with the larger token budget a dense
                            # 8,000-char SOW chunk can legitimately produce a
                            # couple hundred facts, and the old ceiling was
                            # failing real extractions as if they were
                            # degenerate ones.

# ── Shared ledger-fact JSON contract ─────────────────────────────────────────
#
# Every extraction path (text, recording, image) targets this exact shape so
# one validator and one downstream consumer (the Celery tasks in
# app.workers.tasks.sow_ledger) serve all three source kinds.

_LEDGER_RESPONSE_SHAPE = (
    '{"facts": [{"fact_type": "feature"|"decision"|"ui_element"|'
    '"open_question", "element_type": "button"|"dropdown"|"filter"|'
    '"checkbox"|"toggle"|"slider"|"three_dot_menu"|"tab"|"modal"|"other"|'
    'null, "label": str, "location": str|null, "behavior_notes": str|null, '
    '"source_ref": str|null}]}'
)

_SOURCE_REF_RULE = (
    "\n\n'source_ref': a short pointer back to exactly where this came from "
    "— a timestamp if one is spoken/shown (e.g. \"00:14:32\"), a short "
    "verbatim quote, or a description of the specific moment/screen (e.g. "
    "\"third screen shared, top-right corner\"). Null only if genuinely "
    "impossible to pinpoint. This is what lets a reviewer verify the fact "
    "against the original source later — do not skip it to save effort."
)

_LEDGER_RULES = (
    "fact_type meanings:\n"
    "- \"feature\": a distinct piece of functionality (e.g. \"Bulk delete "
    "for the skills list\").\n"
    "- \"decision\": an explicit decision/agreement (e.g. \"Sort defaults "
    "to newest first\").\n"
    "- \"ui_element\": ONE SPECIFIC interactive control — a button, "
    "dropdown, filter, checkbox, toggle, slider, three-dot/kebab menu, tab, "
    "or modal. Extract EVERY individual control separately, even ones "
    "mentioned only in passing or merely implied by describing a workflow "
    "(e.g. \"the user can filter and sort the table\" implies AT LEAST a "
    "filter control and a sort dropdown — extract both as separate facts, "
    "never merge multiple controls into one vague fact).\n"
    "- \"open_question\": something left unresolved/ambiguous that a "
    "developer or tester would need clarified before building/testing it.\n"
    "\n"
    "For ui_element facts: set element_type to the closest match from the "
    "enum (use 'other' only if genuinely none fit). label = the control's "
    "name/label. location = where it appears if stated (page/section/"
    "panel) — null if not stated, never guessed. behavior_notes = what it "
    "does, its options/values, validation rules — only what was actually "
    "shown or said.\n"
    "\n"
    "CRITICAL — exhaustiveness over brevity: this ledger is the ONLY "
    "checklist a later completeness audit and a vibe-testing pipeline will "
    "have. A button, dropdown, filter, checkbox, toggle, slider, or menu "
    "that is folded into a paragraph-level 'feature' fact instead of "
    "listed as its own 'ui_element' fact will be invisible to that audit "
    "and never get tested — this is a real business-impact failure mode, "
    "not a style preference. When in doubt, extract MORE separate "
    "ui_element facts, not fewer.\n"
    "\n"
    "Do not invent facts that weren't shown/discussed/clearly implied — "
    "leave genuine gaps as open_question facts instead of guessing. Return "
    '{"facts": []} if nothing extractable.'
) + _SOURCE_REF_RULE


class _NeedsSplit(IngestError):
    """This chunk could not be extracted whole — retry it in smaller pieces.

    Raised for the two failure modes that a smaller input actually fixes:
    the model's response was cut off by max_tokens, or it returned an
    implausible number of facts. Deliberately an IngestError subclass so the
    existing retry/failure plumbing in _extract_chunks treats it as a chunk
    failure if it ever escapes the splitter.
    """


def _validate_ledger_fact(item: object) -> dict | None:
    """Return a normalized ledger-fact dict, or None if schema-invalid.
    Never raises — invalid entries are dropped by the caller, which logs
    how many were dropped (same philosophy as design_ingest._validate_checkpoint)."""
    if not isinstance(item, dict):
        return None
    fact_type = item.get("fact_type")
    if fact_type not in _VALID_FACT_TYPES:
        return None
    label = str(item.get("label") or "").strip()
    if not label:
        return None

    element_type = item.get("element_type")
    if fact_type == "ui_element":
        if element_type not in _VALID_ELEMENT_TYPES:
            element_type = "other"
    else:
        element_type = None  # only meaningful for ui_element facts

    location = str(item.get("location") or "").strip()[:_MAX_LOCATION_CHARS] or None
    behavior_notes = (
        str(item.get("behavior_notes") or "").strip()[:_MAX_NOTES_CHARS] or None
    )
    source_ref = str(item.get("source_ref") or "").strip()[:_MAX_SOURCE_REF_CHARS] or None

    return {
        "fact_type": fact_type,
        "element_type": element_type,
        "label": label[:_MAX_LABEL_CHARS],
        "location": location,
        "behavior_notes": behavior_notes,
        "source_ref": source_ref,
    }


def _validate_facts(
    raw_items: list, *, source_label: str, on_overflow: str = "raise"
) -> list[dict]:
    """Validate and normalize the LLM's fact list.

    on_overflow controls what happens when the model returns more than
    _MAX_FACTS_PER_CALL facts:

      "split"     -- raise _NeedsSplit so the caller re-extracts this chunk in
                     smaller pieces. Correct for the chunked document paths,
                     where a smaller input genuinely fixes the problem.
      "raise"     -- fail loudly (SOW_CHUNKING_PLAN Phase 4, D5). Correct for
                     the single-shot image/recording paths, which have nothing
                     to split. The original behaviour silently sliced the list,
                     so a dense screen returning 240 facts lost 40 with no log
                     line distinguishing it from a genuine 200 — silent
                     truncation of a ledger whose entire purpose is
                     exhaustiveness is the exact failure mode this pipeline
                     claims not to have.
      "truncate"  -- legacy behaviour, kept for callers where a hard ceiling
                     is genuinely the right answer.
    """
    items = raw_items if isinstance(raw_items, list) else []

    if len(items) > _MAX_FACTS_PER_CALL:
        if on_overflow == "split":
            raise _NeedsSplit(
                f"Extraction from {source_label} returned {len(items)} facts, "
                f"over the {_MAX_FACTS_PER_CALL} ceiling — re-extracting in "
                "smaller pieces."
            )
        if on_overflow == "raise":
            raise IngestError(
                f"Extraction from {source_label} returned {len(items)} facts, "
                f"over the {_MAX_FACTS_PER_CALL} ceiling. This is usually a "
                "degenerate/repeating model response rather than a genuinely "
                "huge screen. Retry this source; if it persists, split the "
                "document."
            )
        logger.warning(
            "SOW ledger: truncating %d fact(s) to %d from %s",
            len(items), _MAX_FACTS_PER_CALL, source_label,
        )
        items = items[:_MAX_FACTS_PER_CALL]

    facts = [f for f in (_validate_ledger_fact(i) for i in items) if f]
    dropped = len(items) - len(facts)
    if dropped:
        logger.warning(
            "SOW ledger: dropped %d schema-invalid fact(s) from %s", dropped, source_label
        )
    return facts


# ── Shared chunk-loop machinery ──────────────────────────────────────────────


def _chunk_label(chunk: Chunk) -> str:
    """Human-readable identification of a chunk for error messages -- part
    number plus section, so a failure tells the user WHERE it failed rather
    than just that something did."""
    if chunk.total == 1:
        return "the document"
    section = " > ".join(chunk.heading_path)
    return f"part {chunk.index} of {chunk.total}" + (f" ('{section}')" if section else "")


def _report(on_progress, stage: str, current: int, total: int) -> None:
    """Fire a progress callback without ever letting it break extraction.

    Progress reporting is display-only: it commits a row in the worker's DB
    session. A failure there (dropped connection, poisoned session) must not
    lose an LLM pass that has already been paid for -- so it is logged and
    swallowed, and the source simply keeps rendering its last known
    progress until the terminal done/error write.
    """
    if on_progress is None:
        return
    try:
        on_progress(stage, current, total)
    except Exception:  # noqa: BLE001
        logger.warning(
            "SOW ledger: progress callback failed at %s %d/%d (ignored)",
            stage, current, total, exc_info=True,
        )


def _subdivide(chunk: Chunk) -> list[Chunk]:
    """Split one chunk's text roughly in half, preserving its framing.

    The halves inherit the parent's heading_path, locator and context_header
    so the model keeps the same "where am I in the document" framing it had
    before — losing that is what the SOW_CHUNKING_PLAN Phase 3 work existed
    to prevent, and a truncation retry must not quietly undo it. Only `text`
    and the part numbering differ.

    Returns [] when the chunk is already too small to split usefully; the
    caller treats that as "cannot recover by splitting".
    """
    text = chunk.text
    if len(text) < 400:
        return []

    target = len(text) // 2
    # Prefer a paragraph boundary near the midpoint so a split never lands
    # mid-sentence; fall back to the exact midpoint if there is none.
    split_at = text.rfind("\n\n", 0, target)
    if split_at <= 0:
        split_at = text.find("\n\n", target)
    if split_at <= 0:
        split_at = target

    halves = [text[:split_at].strip(), text[split_at:].strip()]
    halves = [h for h in halves if h]
    if len(halves) < 2:
        return []

    return [
        Chunk(
            index=chunk.index,
            total=chunk.total,
            text=half,
            heading_path=list(chunk.heading_path),
            locator=chunk.locator,
            strategy=chunk.strategy,
            context_header=chunk.context_header,
        )
        for half in halves
    ]


def _incomplete_marker_fact(chunk: Chunk, reason: str) -> dict:
    """A synthetic open_question recording that extraction could not finish.

    A gap the ledger cannot see is a gap nobody reviews. Rather than return
    a quietly short fact list, the unrecoverable case leaves a visible row
    that flows into the drafted SOW as a warning callout and into review as
    an explicit open question.
    """
    section = " > ".join(chunk.heading_path) or "an unnamed section"
    return {
        "fact_type": "open_question",
        "element_type": None,
        "label": f"Extraction incomplete for {section}"[:_MAX_LABEL_CHARS],
        "location": section[:_MAX_LOCATION_CHARS],
        "behavior_notes": (
            f"Automated extraction of this part of the document did not complete "
            f"({reason}). Requirements from this section may be missing from the "
            "ledger — review the source document for this section manually."
        )[:_MAX_NOTES_CHARS],
        "source_ref": (chunk.locator or _chunk_label(chunk))[:_MAX_SOURCE_REF_CHARS],
    }


def _extract_with_split(
    chunk: Chunk, extractor, *, source_kind: str, depth: int = 0
) -> tuple[list[dict], list[str]]:
    """Extract one chunk, halving and recursing if it could not be done whole.

    Returns (facts, models_used). Raises IngestError for genuine extraction
    failures (provider down, unparseable response) — only _NeedsSplit, which
    means "the answer didn't fit", is handled here.

    This is the structural half of the truncation fix. Raising max_tokens and
    shrinking the default chunk budget make truncation rare;
    llm_router.complete_json_complete escalates the budget once when it
    happens anyway; and this splits the input when even that is not enough.
    Between them, an over-long section is re-asked in pieces instead of
    returning a silently shortened answer.
    """
    try:
        facts, model_used = extractor(chunk)
        return facts, [model_used]
    except _NeedsSplit as exc:
        if depth >= _MAX_SPLIT_DEPTH:
            logger.error(
                "SOW ledger: %s %s still incomplete at split depth %d — recording "
                "the gap explicitly: %s",
                source_kind, _chunk_label(chunk), depth, exc,
            )
            return [_incomplete_marker_fact(chunk, str(exc))], []

        halves = _subdivide(chunk)
        if not halves:
            logger.error(
                "SOW ledger: %s %s cannot be split further — recording the gap: %s",
                source_kind, _chunk_label(chunk), exc,
            )
            return [_incomplete_marker_fact(chunk, str(exc))], []

        logger.warning(
            "SOW ledger: %s %s did not fit in one response (%s) — re-extracting "
            "as %d smaller piece(s) at depth %d",
            source_kind, _chunk_label(chunk), exc, len(halves), depth + 1,
        )
        facts: list[dict] = []
        models: list[str] = []
        for half in halves:
            half_facts, half_models = _extract_with_split(
                half, extractor, source_kind=source_kind, depth=depth + 1
            )
            facts.extend(half_facts)
            for m in half_models:
                if m not in models:
                    models.append(m)
        return facts, models


def _extract_chunks(
    chunks: list[Chunk], extractor, *, source_kind: str, on_progress=None
) -> tuple[list[dict], str, list[str]]:
    """Run `extractor` over every chunk, retrying transient failures.

    Returns (facts, models_used, failures). `failures` is a list of
    human-readable "part N of M ('Section'): reason" strings — empty on a
    fully clean run. The caller's task layer maps a non-empty `failures` to
    SowSourceStatus.done_with_errors and surfaces the list.

    `on_progress(stage, current, total)` is called once before the first
    chunk and once after each chunk finishes (successfully or not), so the
    UI can show real "part N of M" progress instead of a static badge. It is
    optional and best-effort -- see _report.

    Partial-result policy. SOW_CHUNKING_PLAN Phase 4 (defect D4) made this
    all-or-nothing: one failed chunk discarded every other chunk's facts,
    because "an incomplete ledger silently becoming a SOW baseline" is a
    business-impact failure. The reasoning was right; the remedy was too
    blunt once documents routinely split into ~18 parts, where a single
    provider blip threw away seventeen successful extractions and the user's
    only recourse was to re-upload and re-pay for all of them.

    The fix keeps the reasoning and changes the remedy: partial results are
    saved, but never *silently* — the source lands in done_with_errors (not
    done), the failing parts are named, and the UI offers a retry. The
    invariant that matters is "no incomplete ledger is mistaken for a
    complete one", and a distinct terminal status enforces that as well as
    discarding the work did, without the collateral damage. Only a run where
    EVERY chunk failed still raises: that has nothing worth keeping and no
    partial state worth explaining.
    """
    all_facts: list[dict] = []
    models_used: list[str] = []
    failures: list[str] = []

    total = len(chunks)
    _report(on_progress, "extracting", 0, total)

    for index, chunk in enumerate(chunks, start=1):
        last_error: Exception | None = None
        for attempt in range(1, _CHUNK_ATTEMPTS + 1):
            try:
                facts, chunk_models = _extract_with_split(
                    chunk, extractor, source_kind=source_kind
                )
                all_facts.extend(facts)
                for model_used in chunk_models:
                    if model_used not in models_used:
                        models_used.append(model_used)
                last_error = None
                break
            except IngestError as exc:
                last_error = exc
                if attempt < _CHUNK_ATTEMPTS:
                    delay = _CHUNK_BACKOFF_SECONDS[attempt - 1]
                    logger.warning(
                        "SOW ledger: %s %s failed (attempt %d/%d), retrying in %ds: %s",
                        source_kind, _chunk_label(chunk), attempt, _CHUNK_ATTEMPTS,
                        delay, exc,
                    )
                    time.sleep(delay)
        if last_error is not None:
            logger.error(
                "SOW ledger: %s %s failed after %d attempts: %s",
                source_kind, _chunk_label(chunk), _CHUNK_ATTEMPTS, last_error,
            )
            failures.append(f"{_chunk_label(chunk)}: {last_error}")
        # Reported for failed chunks too: the loop continues in order to
        # collect every failure for one message, and freezing the bar on the
        # first failure would look like a hang rather than a run that is
        # still working.
        _report(on_progress, "extracting", index, total)

    if failures and len(failures) == len(chunks):
        _report(on_progress, "extracting", total, total)
        raise IngestError(
            f"Extraction failed for all {len(chunks)} part(s) after "
            f"{_CHUNK_ATTEMPTS} attempts each — {'; '.join(failures[:5])}"
            + (" …" if len(failures) > 5 else "")
            + ". No facts were saved; retry this source."
        )

    _report(on_progress, "saving", total, total)
    facts, merged = dedupe_facts(all_facts)
    if merged:
        logger.info(
            "SOW ledger: %s dedup merged %d duplicate fact(s) across %d chunk(s)",
            source_kind, merged, len(chunks),
        )
    if failures:
        logger.warning(
            "SOW ledger: %s completed with %d of %d part(s) failed — %d fact(s) saved",
            source_kind, len(failures), len(chunks), len(facts),
        )
    return facts, ", ".join(models_used), failures


# ── Text transcript extraction ───────────────────────────────────────────────

_EXCERPT_RULE = (
    "\n\nThis is ONE PART of a larger document. Extract only what appears "
    "in the <content> block. If a <preceding_context> block is present, it "
    "is there ONLY so you can resolve references like \"the button above\" "
    "— never extract a fact whose evidence appears solely in that block; "
    "the part that owns it has already extracted it, and doing so again "
    "creates a duplicate requirement."
)

_LOCATION_RULE = (
    "\n\nUse the 'Section path' in <document_context> as the default "
    "'location' for ui_element facts when the text itself does not state "
    "one — it is the section this content genuinely sits in, not a guess. "
    "This is the one case where filling 'location' without an explicit "
    "statement is correct rather than invention."
)


def _chunk_rules(chunk: Chunk) -> str:
    """Prompt rules that depend on the chunk's position in the document.

    Kept separate because they answer different questions: the excerpt rule
    only matters when there IS a neighbouring part to double-extract from,
    while the location rule applies to any chunk that knows its section --
    including a single-chunk document, which is the common case for a small
    SOW and exactly where a null `location` is most avoidable.
    """
    rules = ""
    if chunk.total > 1:
        rules += _EXCERPT_RULE
    if chunk.heading_path:
        rules += _LOCATION_RULE
    return rules


def extract_ledger_from_text(chunk: Chunk) -> tuple[list[dict], str]:
    """Extract ledger facts from one chunk of a meeting transcript. Returns
    (facts, model_used). Raises IngestError on total provider failure.

    Takes a Chunk rather than (text, part_label) since
    SOW_CHUNKING_PLAN Phase 3: the chunk carries its section path, locator
    and preceding-context framing, which is what replaced the bare
    "part 3 of 7" label the model used to get (defect D2).
    """
    from app.services import llm_router

    system = (
        "You are a senior QA/business analyst turning a meeting transcript "
        "for a software product into a structured requirements ledger. "
        "This ledger will later be used to write an exhaustive Statement "
        "of Work and to auto-generate QA test checkpoints — anything you "
        "omit here will be MISSING from both, which has real business "
        "impact. Respond with JSON only:\n"
        f"{_LEDGER_RESPONSE_SHAPE}\n\n{_LEDGER_RULES}"
        + _chunk_rules(chunk)
    )
    prompt = (
        "Extract a requirements ledger from this meeting transcript:\n\n"
        + chunk.prompt_text()
    )

    try:
        result = llm_router.complete_json_complete(
            prompt, system=system, max_tokens=_LEDGER_MAX_TOKENS
        )
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    if result.truncated:
        raise _NeedsSplit(
            f"the response for {_chunk_label(chunk)} was cut off at the token "
            "limit even after escalation"
        )

    raw = result.parsed_json or {}
    items = raw.get("facts", []) if isinstance(raw, dict) else []
    facts = _validate_facts(
        items, source_label=_chunk_label(chunk), on_overflow="split"
    )
    logger.info(
        "SOW ledger: %d fact(s) extracted from transcript %s via %s",
        len(facts), _chunk_label(chunk), result.model_used,
    )
    return facts, result.model_used


def extract_ledger_from_transcript(
    text: str,
    *,
    document_title: str | None = None,
    max_chars: int | None = None,
    on_progress=None,
) -> tuple[list[dict], str, list[str]]:
    """Chunk a (possibly large) transcript on SPEAKER TURNS and extract facts
    from every part. Returns (facts, models_used, failures).

    Chunking moved from design_ingest.chunk_text (fixed character windows)
    to doc_chunking (SOW_CHUNKING_PLAN Phase 3): a turn is the atomic unit
    of a meeting, and cutting one in half separates a decision from the
    person who made it and from the qualifier that followed.

    max_chars=None resolves the budget from doc_chunking.max_chars_for.
    Duplicate facts are merged across chunks, and a partially failed run
    returns its successful facts alongside a non-empty `failures` list rather
    than discarding everything — see _extract_chunks.
    """
    _report(on_progress, "chunking", 0, 0)
    chunks = chunk_document(
        text,
        file_name="transcript.txt",
        doc_kind="transcript",
        document_title=document_title,
        max_chars=max_chars,
    )
    return _extract_chunks(
        chunks,
        extract_ledger_from_text,
        source_kind="transcript",
        on_progress=on_progress,
    )


def _outline_from_chunks(chunks: list[Chunk]) -> list[dict]:
    """The document's own heading structure, in its original order.

    One entry per distinct heading path, first-seen order preserved. This is
    the imported document's table of contents as the chunker actually saw it
    — recorded so "keep the original format" is a property of stored data
    rather than something the drafting model has to be trusted to remember.
    """
    outline: list[dict] = []
    seen: set[tuple[str, ...]] = set()
    for chunk in chunks:
        key = tuple(chunk.heading_path)
        if not key or key in seen:
            continue
        seen.add(key)
        outline.append({
            "heading_path": list(chunk.heading_path),
            "heading": chunk.heading_path[-1],
            "locator": chunk.locator,
        })
    return outline


# ── Existing SOW document extraction (Import SOW, SOW tab) ──────────────────
#
# Same fact schema/validator/chunking as the transcript path above -- the
# only thing that differs is the system prompt's framing, since the input
# here is already-written SOW/requirements prose (headings, numbered
# requirements, tables), not a meeting transcript. Kept as its own function
# rather than reusing extract_ledger_from_text with a flag: this file's
# established convention is one function per source kind (see
# extract_ledger_from_recording / extract_ledger_from_image below), and a
# prompt tuned for "here is a document" reads differently than one tuned for
# "here is a conversation" -- conflating them risks quietly degrading
# whichever framing loses out.

def extract_ledger_from_sow_document(chunk: Chunk) -> tuple[list[dict], str]:
    """Extract ledger facts from one chunk of an uploaded pre-existing
    SOW/requirements document. Returns (facts, model_used). Raises
    IngestError on total provider failure."""
    from app.services import llm_router

    system = (
        "You are a senior QA/business analyst turning an existing Statement "
        "of Work / requirements document for a software product into a "
        "structured requirements ledger. This document is being imported as "
        "the baseline for further authoring on this platform — the ledger "
        "will be used to (re)draft the SOW's sections and to auto-generate "
        "QA test checkpoints for vibe testing. Anything you omit here will "
        "be MISSING from both, which has real business impact. Respond "
        "with JSON only:\n"
        f"{_LEDGER_RESPONSE_SHAPE}\n\n{_LEDGER_RULES}"
        + _chunk_rules(chunk)
    )
    prompt = (
        "Extract a requirements ledger from this existing SOW/requirements "
        "document:\n\n" + chunk.prompt_text()
    )

    try:
        result = llm_router.complete_json_complete(
            prompt, system=system, max_tokens=_LEDGER_MAX_TOKENS
        )
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    if result.truncated:
        raise _NeedsSplit(
            f"the response for {_chunk_label(chunk)} was cut off at the token "
            "limit even after escalation"
        )

    raw = result.parsed_json or {}
    items = raw.get("facts", []) if isinstance(raw, dict) else []
    facts = _validate_facts(
        items, source_label=_chunk_label(chunk), on_overflow="split"
    )
    # Stamp each fact with the heading it physically came from. The model is
    # never asked for this -- it is the chunker's own structural knowledge,
    # so it cannot be hallucinated -- and it is what lets a regenerated SOW
    # reproduce the imported document's section order instead of inventing a
    # fresh outline. See sow_drafting.group_ledger_into_sections.
    if chunk.heading_path:
        for fact in facts:
            fact["source_heading_path"] = list(chunk.heading_path)
    logger.info(
        "SOW ledger: %d fact(s) extracted from existing SOW document %s via %s",
        len(facts), _chunk_label(chunk), result.model_used,
    )
    return facts, result.model_used


def extract_ledger_from_sow_document_full(
    source: str | list[Block],
    *,
    file_name: str = "document.txt",
    document_title: str | None = None,
    max_chars: int | None = None,
    on_progress=None,
) -> tuple[list[dict], str, list[str], list[dict]]:
    """Chunk a (possibly large) existing-SOW document on its real structure
    and extract facts from every part.

    Returns (facts, models_used, failures, outline). `outline` is the
    document's own heading structure in its original order — see
    `_outline_from_chunks`; it is what lets a regenerated SOW mirror the
    imported document instead of inventing a fresh set of headings.

    `source` should be the block list from
    sow_import.extract_existing_sow_blocks() so headings, tables and page
    markers are available to the chunker — a plain string still works but
    falls back to paragraph splitting, which is what this plan replaced.

    Duplicate facts are merged across chunks; a partially failed run returns
    its successful facts with a non-empty `failures` list. See
    _extract_chunks.
    """
    _report(on_progress, "chunking", 0, 0)
    chunks = chunk_document(
        source,
        file_name=file_name,
        doc_kind="sow_document",
        document_title=document_title,
        max_chars=max_chars,
    )
    facts, models, failures = _extract_chunks(
        chunks,
        extract_ledger_from_sow_document,
        source_kind="existing SOW document",
        on_progress=on_progress,
    )
    return facts, models, failures, _outline_from_chunks(chunks)


# ── Meeting recording extraction (extends video_ingest.py) ──────────────────

_RECORDING_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


def recording_mime_for(file_name: str) -> str:
    ext = os.path.splitext(file_name.lower())[1]
    mime = _RECORDING_MIME_BY_EXT.get(ext)
    if not mime:
        raise IngestError(
            f"Unsupported recording format '{ext}'. Use .mp4, .webm, .mov, "
            ".mp3, .m4a, .wav, or .ogg."
        )
    return mime


def recording_duration_seconds(storage_path: str) -> float | None:
    """Best-effort duration check, reusing video_ingest's ffprobe helper --
    works for audio-only files too (ffprobe reads container duration
    regardless of stream type). None if ffprobe is unavailable/fails; the
    caller must not hard-block an upload on a missing ffprobe binary, only
    on a duration it actually measured."""
    from app.services.video_ingest import _ffprobe_duration

    return _ffprobe_duration(storage_path)


def _build_recording_prompt(context_label: str | None) -> str:
    context = (
        f" The uploader described this recording as: \"{context_label}\"."
        if context_label else ""
    )
    return (
        "You are a senior QA/business analyst listening to and watching a "
        "recording of a product requirements/planning meeting."
        f"{context} Extract a structured requirements ledger from "
        "EVERYTHING said (transcribe and analyze the full audio track) and "
        "EVERYTHING shown on screen (if any screen-sharing occurs). "
        "Respond with JSON only:\n"
        f"{_LEDGER_RESPONSE_SHAPE}\n\n{_LEDGER_RULES}\n\n"
        "If the recording includes screen-sharing of a product's UI, pay "
        "close attention to small persistent chrome — headers, sidebars, "
        "toolbars, per-row action menus — not just the main content area "
        "being narrated; these are exactly the elements a viewer's summary "
        "would normally skip but a QA checklist cannot. Still images "
        "extracted from the same recording, if provided below, are there "
        "specifically so you can read small on-screen text/controls with "
        "confidence — inspect them for any control the audio narration "
        "didn't explicitly call out.\n\n"
        'Return {"facts": []} only if the recording genuinely contains no '
        "extractable requirements discussion (e.g. it's silent or purely "
        "off-topic) — this should be rare."
    )


def extract_ledger_from_recording(
    storage_path: str, file_name: str, *, context_label: str | None = None
) -> tuple[list[dict], str]:
    """Full pipeline for a meeting recording: upload to Gemini Files API,
    wait for processing, extract still frames for UI precision, run one
    generateContent call with a ledger-focused prompt, clean up the remote
    file. Reuses video_ingest.py's proven HTTP/upload/polling machinery
    directly rather than re-implementing it — only the prompt and the
    response shape parsed differ from digest_video().

    Unlike digest_video(), there is no platform_match hard gate here: a
    meeting recording is a discussion, not necessarily a product
    walkthrough, so there is no single declared product name to verify
    on-screen branding against. context_label (optional, user-supplied) is
    passed through as free-text framing only.

    Raises IngestError on failure -- never returns an empty "success".
    """
    from app.services.video_ingest import (
        _api_key,
        _delete_file,
        _extract_still_frames,
        _generate,
        _upload_video,
        _wait_until_active,
    )

    mime_type = recording_mime_for(file_name)
    key = _api_key()
    prompt = _build_recording_prompt(context_label)

    still_frames = _extract_still_frames(storage_path)  # [] for audio-only, best-effort
    logger.info(
        "SOW ledger: extracted %d still frame(s) from %s", len(still_frames), file_name
    )

    primary = os.environ.get("VISUAL_VIDEO_MODEL", "").strip() or "gemini-3.5-flash"
    fallback = os.environ.get("VISUAL_VIDEO_FALLBACK", "").strip() or "gemini-2.5-flash"
    models = [primary] + ([fallback] if fallback != primary else [])

    file_info = _upload_video(storage_path, mime_type, key)
    remote_name = file_info.get("name", "")
    file_uri = file_info["uri"]
    logger.info("SOW ledger: uploaded %s as %s", file_name, remote_name)

    try:
        if file_info.get("state") != "ACTIVE":
            _wait_until_active(remote_name, key)

        text: str | None = None
        model_used = models[0]
        last_error: Exception | None = None
        for model in models:
            try:
                text = _generate(model, file_uri, mime_type, key, prompt, still_frames)
                model_used = model
                break
            except Exception as exc:  # noqa: BLE001 — mirrors video_ingest's per-model fallback
                last_error = exc
                logger.warning("SOW ledger: %s failed for recording digest: %s", model, exc)
        if text is None:
            raise IngestError(f"All Gemini models failed for the recording digest: {last_error}")

        import json

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestError("Gemini recording digest was not valid JSON.") from exc
        if not isinstance(raw, dict):
            raise IngestError("Gemini recording digest returned an unexpected shape.")

        items = raw.get("facts", [])
        facts = _validate_facts(items, source_label=file_name)
        logger.info(
            "SOW ledger: %d fact(s) extracted from recording %s via %s",
            len(facts), file_name, model_used,
        )
        return facts, model_used
    finally:
        if remote_name:
            _delete_file(remote_name, key)


# ── Design reference (image) extraction ──────────────────────────────────────

def extract_ledger_from_image(
    image_bytes: bytes, file_name: str, *, context_label: str | None = None
) -> tuple[list[dict], str]:
    """Vision-based ledger extraction for a design reference (Figma export
    or uploaded screenshot). Uses llm_router.complete's image input, not
    the Gemini-only Files API path (a single still image needs no upload/
    poll lifecycle — this is a plain multimodal completion call)."""
    from app.services import llm_router

    context = (
        f" The uploader described this design as: \"{context_label}\"."
        if context_label else ""
    )
    system = (
        "You are a senior QA/business analyst reading a design reference "
        f"image (a screen mockup or product screenshot).{context} Extract "
        "a structured requirements ledger describing every UI element "
        "visible in the image. Respond with JSON only:\n"
        f"{_LEDGER_RESPONSE_SHAPE}\n\n{_LEDGER_RULES}\n\n"
        "Since this is a single static image (not a recording), most "
        "facts will naturally be 'ui_element' — scan the ENTIRE image "
        "systematically (header, sidebar, main content, footer, any "
        "visible menus/modals) rather than only the most visually "
        "prominent controls. 'feature'/'decision'/'open_question' facts "
        "only apply if the image itself contains explanatory text/"
        "annotations conveying them."
    )
    prompt = "Extract a requirements ledger from this design reference image."

    try:
        result = llm_router.complete_json_complete(
            prompt,
            system=system,
            images_b64=[base64.b64encode(image_bytes).decode("ascii")],
            max_tokens=_LEDGER_MAX_TOKENS,
        )
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    if result.truncated:
        # A single image cannot be "split into smaller pieces" the way a
        # document chunk can, so this is reported rather than recovered.
        logger.warning(
            "SOW ledger: design image %s produced a truncated response even after "
            "escalation — some on-screen controls may be missing", file_name,
        )

    raw = result.parsed_json or {}
    items = raw.get("facts", []) if isinstance(raw, dict) else []
    facts = _validate_facts(items, source_label=file_name)
    logger.info(
        "SOW ledger: %d fact(s) extracted from design image %s via %s",
        len(facts), file_name, result.model_used,
    )
    return facts, result.model_used
