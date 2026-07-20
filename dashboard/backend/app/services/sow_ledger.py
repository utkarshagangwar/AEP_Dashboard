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

from app.core.logging import get_logger
from app.services.design_ingest import IngestError, chunk_text

logger = get_logger(__name__)

_VALID_FACT_TYPES = {"feature", "decision", "ui_element", "open_question"}
_VALID_ELEMENT_TYPES = {
    "button", "dropdown", "filter", "checkbox", "toggle", "slider",
    "three_dot_menu", "tab", "modal", "other",
}
_MAX_LABEL_CHARS = 500
_MAX_LOCATION_CHARS = 500
_MAX_NOTES_CHARS = 2000
_MAX_SOURCE_REF_CHARS = 300
_MAX_FACTS_PER_CALL = 200  # sanity ceiling — a single call returning more than
                            # this is almost certainly a degenerate/repeating
                            # response, not a genuinely huge screen.

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


def _validate_facts(raw_items: list, *, source_label: str) -> list[dict]:
    items = raw_items[:_MAX_FACTS_PER_CALL] if isinstance(raw_items, list) else []
    facts = [f for f in (_validate_ledger_fact(i) for i in items) if f]
    dropped = len(items) - len(facts)
    if dropped:
        logger.warning(
            "SOW ledger: dropped %d schema-invalid fact(s) from %s", dropped, source_label
        )
    return facts


# ── Text transcript extraction ───────────────────────────────────────────────

def extract_ledger_from_text(text: str, *, part_label: str | None = None) -> tuple[list[dict], str]:
    """Extract ledger facts from a meeting transcript (or an excerpt of a
    large one — see extract_ledger_from_transcript for chunking). Returns
    (facts, model_used). Raises IngestError on total provider failure."""
    from app.services import llm_router

    system = (
        "You are a senior QA/business analyst turning a meeting transcript "
        "for a software product into a structured requirements ledger. "
        "This ledger will later be used to write an exhaustive Statement "
        "of Work and to auto-generate QA test checkpoints — anything you "
        "omit here will be MISSING from both, which has real business "
        "impact. Respond with JSON only:\n"
        f"{_LEDGER_RESPONSE_SHAPE}\n\n{_LEDGER_RULES}"
    )
    prompt = "Extract a requirements ledger from this meeting transcript"
    if part_label:
        prompt += (
            f" ({part_label} of a larger transcript — this is an excerpt; "
            "extract only what appears in this excerpt)"
        )
    prompt += ":\n\n" + text

    try:
        result = llm_router.complete(prompt, system=system, expect_json=True, max_tokens=8192)
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    raw = result.parsed_json or {}
    items = raw.get("facts", []) if isinstance(raw, dict) else []
    facts = _validate_facts(items, source_label=part_label or "transcript")
    logger.info(
        "SOW ledger: %d fact(s) extracted from transcript via %s", len(facts), result.model_used
    )
    return facts, result.model_used


def extract_ledger_from_transcript(text: str) -> tuple[list[dict], str]:
    """Chunk a (possibly large) transcript with design_ingest.chunk_text and
    concatenate facts across chunks — same chunking mechanism the SOW
    Checkpoints pipeline already uses, so a long transcript never silently
    truncates. Simple concatenation, no cross-chunk dedup (Phase 1 scope;
    Phase 2's constrained regrouping is where cross-source consolidation
    belongs). Raises IngestError only if EVERY chunk fails."""
    chunks = chunk_text(text)
    all_facts: list[dict] = []
    models_used: list[str] = []
    errors: list[str] = []

    for i, chunk in enumerate(chunks, start=1):
        part_label = f"part {i} of {len(chunks)}" if len(chunks) > 1 else None
        try:
            facts, model_used = extract_ledger_from_text(chunk, part_label=part_label)
            all_facts.extend(facts)
            if model_used not in models_used:
                models_used.append(model_used)
        except IngestError as exc:
            logger.warning("SOW ledger: transcript chunk %d/%d failed: %s", i, len(chunks), exc)
            errors.append(str(exc))

    if not models_used:
        raise IngestError(f"All transcript chunks failed: {'; '.join(errors)}")
    return all_facts, ", ".join(models_used)


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
        result = llm_router.complete(
            prompt,
            system=system,
            images_b64=[base64.b64encode(image_bytes).decode("ascii")],
            expect_json=True,
            max_tokens=8192,
        )
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    raw = result.parsed_json or {}
    items = raw.get("facts", []) if isinstance(raw, dict) else []
    facts = _validate_facts(items, source_label=file_name)
    logger.info(
        "SOW ledger: %d fact(s) extracted from design image %s via %s",
        len(facts), file_name, result.model_used,
    )
    return facts, result.model_used
