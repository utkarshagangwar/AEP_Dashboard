"""Platform-agnostic TDD / Skill extraction engine.

WHY THIS MODULE EXISTS
======================
The original single-pass extractor (app.services.design_ingest._SOW_SYSTEM)
asked one LLM call to "turn a SOW into QA checkpoints". It had three
structural defects, all of which show up as the same user-visible symptom —
"the agent turns the whole SOW into TDDs":

  1. NO TESTABILITY GATE. The prompt never defined what is *not* a
     requirement. A SOW is mostly not requirements: project overview,
     commercial terms, milestones, resourcing, assumptions, dependencies,
     out-of-scope, sign-off process, glossary. With no exclusion rule the
     model dutifully converted every paragraph into a "checkpoint", and
     _validate_checkpoint's deliberate never-drop policy (correct for a
     *vague requirement*) then preserved every one of them.

  2. NO VARIANT MODEL. The prompt said "ONE CHECKPOINT PER FEATURE", so
     each behaviour produced exactly one happy path. Negative and edge
     coverage existed only as free-text 'notes', which are rendered into
     the skill markdown but are never independently executable. Coverage
     was therefore ~100% positive by construction.

  3. NO BEHAVIOUR TAXONOMY. Whether a requirement is an input form, an
     RBAC rule, a scoring formula or an AI generation surface determines
     *which* negative and edge cases are mandatory. Nothing in the pipeline
     ever classified the behaviour, so nothing could demand the right
     probes.

This module replaces text-shaped extraction with behaviour-shaped
extraction, in six stages:

  Stage 0  Zoning        — segment the text, discard non-testable zones
                           (recorded, never silently dropped).
  Stage 1  Behaviours    — extract actor/trigger/response/rule tuples,
                           not paragraphs.
  Stage 2  Categorise    — map each behaviour onto CATEGORIES (below).
  Stage 3  Variants      — the category declares which of
                           positive/negative/edge are MANDATORY; each
                           variant is an independently runnable skill.
  Stage 4  Validate      — deterministic backstop: the category's required
                           variants are checked in code, not trusted from
                           the model.
  Stage 4c Cap           — bound one behaviour's variant count by PRIORITY,
                           keeping one of every test type, and log what was
                           dropped. Replaces an accidental truncation at the
                           output-token limit with a deliberate, visible one.
  Stage 4b Repair        — re-ask for ONLY the variants Stage 4 found
                           missing, then recompute the gap from the result.
                           Detecting a hole and shipping it anyway is still
                           a hole; this fills what it can and leaves the
                           flag standing on what it cannot.
  Stage 5  Dedupe + score — cross-variant dedupe and a coverage scorecard.
  Stage 6  Reconcile     — DOCUMENT level, not part level: merge the same
                           behaviour described in two different parts (a
                           summary section and a detail section), which no
                           per-part stage can see.

DESIGN INVARIANTS (carried over from the existing pipeline — do not break)
=========================================================================
  * Nothing is silently lost. An excluded zone is RECORDED with its reason
    and returned to the caller for storage/display. An under-specified
    requirement is still FLAGGED, never dropped.
  * Fail open. Every LLM stage degrades to "treat everything as testable"
    rather than dropping content. Only total provider failure raises.
  * Trust evidence, not claims. The model's own review_status/category
    claims are re-derived in code where the evidence allows it.
  * Derived != stated. A negative/edge case is almost always QA reasoning,
    not something the document said. It is labelled grounding="derived" so
    nobody mistakes it for a stated requirement.

CONFIG (all opt-OUT, matching the SOW_AUTO_ANALYZE_PARTS convention)
====================================================================
  TDD_EXTRACTION_V2=0     fall back to the legacy single-pass extractor
  TDD_ZONING=0            skip Stage 0 (extract from the whole part)
  TDD_DERIVED_AS_SKILLS=0 keep derived negative/edge checkpoints out of the
                          Skills table (they stay in the checkpoint list)
  TDD_GAP_REPAIR=0        skip Stage 4b (leave a missing required variant
                          flagged as a coverage_gap instead of re-asking for
                          it)
  TDD_RECONCILE=0         skip Stage 6 (no cross-part merging; the same
                          feature described in a summary and again in a
                          detail section stays as two sets of checkpoints)
  TDD_VARIANT_CAP=0       no per-behaviour variant ceiling; a behaviour keeps
                          every variant the model produced
"""
from __future__ import annotations

import difflib
import os
import re

from app.core.logging import get_logger
from app.services.doc_blocks import IngestError

logger = get_logger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean env flag. Opt-out convention: unset means enabled."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return raw not in ("0", "false", "False", "no", "off")


def v2_enabled() -> bool:
    return _flag("TDD_EXTRACTION_V2")


def zoning_enabled() -> bool:
    return _flag("TDD_ZONING")


def derived_as_skills() -> bool:
    return _flag("TDD_DERIVED_AS_SKILLS")


def gap_repair_enabled() -> bool:
    return _flag("TDD_GAP_REPAIR")


def reconcile_enabled() -> bool:
    return _flag("TDD_RECONCILE")


def variant_cap_enabled() -> bool:
    return _flag("TDD_VARIANT_CAP")


# A zoning result that excludes almost the entire part is far more likely to
# be a classifier malfunction than a document that genuinely contains no
# requirements. Above this ratio the zoning verdict is discarded and the whole
# part is extracted from — losing tokens is recoverable, losing requirements
# is not. Mirrors design_ingest/sow_drafting's alarm-ratio safety nets.
_ZONING_MAX_EXCLUSION_RATIO = 0.85
# Segments shorter than this are never zoned on their own — a stray heading or
# one-line fragment carries too little signal to classify, so it rides along
# with its neighbours.
_MIN_SEGMENT_CHARS = 60
_MAX_SEGMENTS_PER_CALL = 60
_MAX_ZONE_REASON_CHARS = 300
_MAX_BEHAVIOR_KEY_CHARS = 120


# ── Vocabulary ───────────────────────────────────────────────────────────────

TEST_TYPES = ("positive", "negative", "edge")
GROUNDINGS = ("stated", "derived")
PRIORITIES = ("smoke", "sanity", "regression")

DEFAULT_TEST_TYPE = "positive"
DEFAULT_GROUNDING = "stated"
DEFAULT_PRIORITY = "regression"

# Acceptance gate on scorecard()["negative_edge_ratio"] — see
# TDD_EXTRACTION_SPEC.md §10. The single number that says whether extraction
# is still doing its job: the defect this module was written to fix produced
# a ratio of ~0.0 by construction, so anything approaching that means the
# extractor has drifted back to happy-path-only output.
#
# This is a QUALITY gate, not a correctness one. Falling below it never fails
# a parse or discards checkpoints — the extraction may be perfectly correct on
# a genuinely thin section. It warns, because a quality regression that nobody
# is told about is only discovered by reading every generated skill by hand,
# which is exactly how the original defect survived as long as it did.
NEGATIVE_EDGE_RATIO_GATE = 0.40

# Below this many checkpoints the ratio is statistically meaningless — a part
# with two checkpoints is either 0%, 50% or 100% and none of those readings
# say anything about extraction quality. Warning on them would train everyone
# to ignore the warning, which costs more than the missed signal.
_RATIO_GATE_MIN_CHECKPOINTS = 4


# ── Stage 0 vocabulary: what a document contains that is NOT a requirement ───
#
# Platform-agnostic on purpose. These are the section kinds every commercial
# SOW/PRD/BRD carries regardless of product domain. Each entry is
# (zone_kind, heading regex, why it is not testable).
NON_TESTABLE_ZONES: tuple[tuple[str, str, str], ...] = (
    ("commercial", r"\b(pricing|cost|commercial|payment terms|invoic|budget|rate card|fees?)\b",
     "commercial terms — describes what is paid, not how the product behaves"),
    ("schedule", r"\b(timeline|milestone|schedule|sprint plan|delivery plan|phases? *&? *duration|effort estimat)\b",
     "delivery schedule — a project plan, not product behaviour"),
    ("resourcing", r"\b(team (structure|composition)|resourc|staffing|roles? (and|&) responsibilit|raci|org chart)\b",
     "project resourcing — describes who does the work, not what the system does"),
    ("assumptions", r"\b(assumptions?|dependenc(y|ies)|pre-?requisites? for (the )?(project|engagement))\b",
     "project assumption/dependency — a delivery precondition, not a system behaviour"),
    ("out_of_scope", r"\b(out[- ]of[- ]scope|exclusions?|not (in scope|included)|non[- ]goals?)\b",
     "explicitly out of scope — must not be tested"),
    ("acceptance_process", r"\b(sign[- ]?off|acceptance (process|procedure|criteria for delivery)|approval process|uat process)\b",
     "acceptance/sign-off process — a governance step, not a product behaviour"),
    ("change_control", r"\b(change (control|request|management)|variation procedure|scope change)\b",
     "change-control procedure — contractual process"),
    ("legal", r"\b(confidential|intellectual property|liabilit|warrant(y|ies)|indemnif|terms (and|&) conditions|nda|governing law)\b",
     "legal/contractual clause"),
    ("glossary", r"\b(glossary|definitions?|abbreviations?|acronyms?|terminology)\b",
     "glossary — vocabulary, contains no behaviour"),
    ("doc_control", r"\b(document (control|history|information)|revision history|version history|change log|table of contents|approvals?)\b",
     "document metadata"),
    ("background", r"\b(executive summary|background|introduction|business (context|objectives?|goals?)|problem statement|about (the )?(client|company|us))\b",
     "narrative background — states business intent, not verifiable system behaviour"),
    ("support_terms", r"\b(support (model|window|hours|sla terms)|maintenance (period|terms)|warranty period|hypercare)\b",
     "post-delivery support terms — contractual, not functional"),
    ("methodology", r"\b(methodolog|ways of working|communication plan|governance|reporting cadence|stand-?up|ceremonies)\b",
     "delivery methodology — how the team works, not how the product works"),
    ("tooling", r"\b(tech(nology)? stack|tools? (and|&) technolog|infrastructure overview|hosting (provider|details)|third[- ]party licen[cs]e)\b",
     "implementation/tooling note — states what it is built with, not what it must do"),
)

_NON_TESTABLE_RE = tuple(
    (kind, re.compile(pattern, re.IGNORECASE), reason)
    for kind, pattern, reason in NON_TESTABLE_ZONES
)

# A heading may match an exclusion pattern while its body still specifies real
# behaviour ("Out of Scope" sections sometimes end with "however, the system
# must still ..."). These markers veto a deterministic exclusion and send the
# segment to the LLM zoner instead of dropping it outright.
_BEHAVIOUR_MARKER_RE = re.compile(
    r"\b(shall|must|should be able to|the (system|user|admin|application|platform) (can|will|shall|must)|"
    r"clicking|on click|when the user|is displayed|is shown|error message|validation|redirect|"
    r"dropdown|button|field|toggle|checkbox|api (returns|responds))\b",
    re.IGNORECASE,
)


# ── Stage 2/3 vocabulary: behaviour categories and their mandatory probes ────
#
# THE CENTRAL IDEA OF THIS MODULE.
#
# A category is not a label for reporting — it is a CONTRACT that declares
# which test types a behaviour of that kind must produce, and what the
# negative/edge probes for that kind actually are. This is what makes the
# extractor platform-agnostic: the probes are properties of the behaviour
# class ("something that accepts user input", "something that computes a
# number", "something that feeds untrusted text to a model"), never of a
# particular product.
#
# Adding a new behaviour class = adding one entry here. No prompt surgery,
# no per-platform branching anywhere else in the pipeline.
#
#   requires : test types that MUST exist for this category. Enforced in
#              code by check_variant_coverage(), not trusted from the model.
#   negative : the failure modes a tester must attempt.
#   edge     : the boundaries a tester must probe.
CATEGORIES: dict[str, dict] = {
    # ── Generic product behaviour ────────────────────────────────────────
    "input_validation": {
        "label": "Input & form validation",
        "when": "the behaviour accepts user-supplied data (a form, a field, a parameter)",
        "requires": ("positive", "negative", "edge"),
        "negative": "required field left empty; wrong format/type; value the rule forbids; submit blocked with a visible message and NO partial record persisted",
        "edge": "minimum and maximum accepted length/value and one step beyond each; whitespace-only; unicode/emoji; leading-trailing spaces; paste vs type",
    },
    "authentication": {
        "label": "Authentication & session",
        "when": "login, logout, OTP, password, token issue/refresh, session lifetime",
        "requires": ("positive", "negative", "edge"),
        "negative": "wrong credential; expired/replayed OTP or token; locked or disabled account; access to a protected route while signed out",
        "edge": "session expiry exactly at the boundary; two concurrent sessions; refresh during an in-flight request; back-button after logout",
    },
    "authorization": {
        "label": "Roles, permissions & access control",
        "when": "a capability is limited to certain roles, tiers, owners or tenants",
        "requires": ("positive", "negative", "edge"),
        "negative": "a role WITHOUT the permission is blocked — in the UI and by calling the same endpoint directly; cross-tenant/other-user record is not reachable by id",
        "edge": "the last remaining privileged user; permission revoked mid-session; a user who belongs to several scopes at once",
    },
    "crud": {
        "label": "Create / read / update / delete",
        "when": "a record is created, listed, edited or removed",
        "requires": ("positive", "negative", "edge"),
        "negative": "duplicate of a value that must be unique; edit or delete of a record that no longer exists; delete of a record something else still references",
        "edge": "empty list state; first and last page of pagination; a record edited by two users at once; delete then re-create with the same identifier",
    },
    "state_transition": {
        "label": "Workflow & state transitions",
        "when": "an entity moves between statuses/stages, or a step gates a later step",
        "requires": ("positive", "negative", "edge"),
        "negative": "a transition the rules forbid is rejected; a gated step attempted before its precondition is met stays locked",
        "edge": "terminal state — no further transition possible; the same transition fired twice; transition while a background job for that entity is in flight",
    },
    "search_filter_sort": {
        "label": "Search, filter & sort",
        "when": "results are queried, narrowed or ordered",
        "requires": ("positive", "negative", "edge"),
        "negative": "a query with no matches shows a defined empty state, not an error or a stale list; a malformed query is handled",
        "edge": "special characters and wildcards in the term; several filters combined; clear/reset restores the full set; sort stability on equal values",
    },
    "file_io": {
        "label": "File upload, download, import & export",
        "when": "a file crosses the system boundary in either direction",
        "requires": ("positive", "negative", "edge"),
        "negative": "unsupported extension; corrupt or truncated file; file over the size limit; upload cancelled mid-transfer",
        "edge": "exactly at the size limit; empty (0-byte) file; non-ASCII filename; replacing an existing file leaves no orphaned copy; exported content matches what is on screen",
    },
    "calculation": {
        "label": "Calculations, totals & derived values",
        "when": "the system computes a number — a score, total, average, percentage, band",
        "requires": ("positive", "negative", "edge"),
        "negative": "a missing or zero operand must not be silently treated as a pass/zero/default; the stated formula is asserted against a known input set, not eyeballed",
        "edge": "exact boundary values on either side of every cut-off; rounding at the stated precision; divide-by-zero and empty-set inputs; partially complete input scored only on what exists",
    },
    "integration": {
        "label": "Third-party & service integration",
        "when": "an external service, webhook, sync or callback is involved",
        "requires": ("positive", "negative", "edge"),
        "negative": "the dependency returns an error or is unreachable — the product degrades visibly instead of hanging or silently no-op-ing",
        "edge": "slow response/timeout; the same callback delivered twice; a partial sync; credentials expiring mid-session",
    },
    "notification": {
        "label": "Notifications, email & messaging",
        "when": "the system sends something to a person outside the UI",
        "requires": ("positive", "negative", "edge"),
        "negative": "delivery failure is surfaced rather than swallowed; the message is NOT sent when the triggering action failed",
        "edge": "the action must have no side effects beyond the message when that is the stated design; duplicate trigger produces one message, not two",
    },
    "payment_billing": {
        "label": "Payment, billing, quota & credits",
        "when": "money, a subscription, a credit balance or a usage quota changes",
        "requires": ("positive", "negative", "edge"),
        "negative": "a declined or failed payment leaves no entitlement granted and no partial record",
        "edge": "retry must not double-charge or double-deduct; balance hitting exactly zero mid-action; refund/rollback path if one is specified",
    },
    "resilience": {
        "label": "Failure handling, retry & recovery",
        "when": "the spec describes interruption, failure, retry, recovery, offline or timeout behaviour",
        "requires": ("positive", "negative", "edge"),
        "negative": "an unrecoverable failure lands in the defined terminal state with no partial write left behind and no partial result shown",
        "edge": "a recoverable interruption and an unrecoverable failure that present identically must not be confused; repeated manual retries produce no duplicate side effect; restart attempted where the spec forbids it is blocked",
    },
    "localization": {
        "label": "Language & localization",
        "when": "more than one language, locale, currency or format is supported",
        "requires": ("positive", "negative", "edge"),
        "negative": "an unsupported locale is not offered and cannot be forced; the selection does not silently fall back without saying so",
        "edge": "the choice propagates to EVERY surface (UI text, generated content, captions, exports, notifications); switching mid-flow; right-to-left and long translated strings not truncating layout",
    },
    "performance_latency": {
        "label": "Response time & duration metrics",
        "when": "a response time, duration or elapsed-time metric is specified or displayed",
        "requires": ("positive", "negative", "edge"),
        "negative": "a long-running operation shows progress instead of appearing frozen, and never times out silently",
        "edge": "first run with no history — empty/zero state rather than an error; the displayed metric matches real elapsed time",
    },
    "data_integrity": {
        "label": "Persistence & data integrity",
        "when": "data must survive a reload, a session, or be consistent across two views",
        "requires": ("positive", "negative", "edge"),
        "negative": "an abandoned or failed operation leaves no half-written record; navigating away with unsaved changes warns rather than silently discarding",
        "edge": "the same entity shown in two places stays consistent; refresh mid-edit; value round-trips through save/reload unchanged (no truncation, no re-encoding)",
    },
    "visual_layout": {
        "label": "Layout, branding & appearance",
        "when": "the requirement is about how something looks, not what it does",
        "requires": ("positive",),
        "negative": "",
        "edge": "",
    },

    # ── AI-specific behaviour (Part A of the Vibe Testing mind map) ───────
    #
    # These exist because an AI surface fails in ways a deterministic one
    # cannot: it degrades silently, it can be talked out of its instructions,
    # and its output shape is a probability rather than a contract.
    "ai_prompt_config": {
        "label": "AI prompt / template configuration",
        "when": "a prompt, template or rulebook that drives AI output is editable or selectable",
        "requires": ("positive", "negative", "edge"),
        "negative": "saving an empty or invalid prompt is blocked and fires no save call; mutually exclusive template options cannot both be selected",
        "edge": "reset-to-default on a scope that never had an override; navigating away with unsaved prompt edits warns; a very long prompt persists byte-for-byte with no truncation — verify the STORED text, not the re-rendered UI",
    },
    "ai_generation": {
        "label": "AI-generated output & response validation",
        "when": "a model produces content, a score, a report or a decision that reaches a user",
        "requires": ("positive", "negative", "edge"),
        "negative": "a malformed, empty or truncated model response produces a defined error state — never a blank screen, a crash, or a plausible-looking placeholder",
        "edge": "unexpected extra fields and missing optional fields do not break rendering; the in-progress state is distinguishable from the final result; two runs on the same input stay within the stated variance",
    },
    "ai_untrusted_input": {
        "label": "Untrusted input reaching a model (prompt injection)",
        "when": "any user-supplied text, file, transcript or third-party content is fed into a prompt",
        "requires": ("positive", "negative", "edge"),
        "negative": "content carrying embedded instructions (\"ignore previous instructions…\", \"award full marks\") does not change the model's behaviour or leak the system prompt",
        "edge": "the same injection delivered through every other channel that reaches the prompt — uploaded file, transcript, imported record, filename — not just the obvious text box",
    },
    "ai_scoring": {
        "label": "AI scoring, grading & confidence bands",
        "when": "a model output is turned into a score, grade, band or pass/fail",
        "requires": ("positive", "negative", "edge"),
        "negative": "an unattempted or empty input must not score as a pass; a score must not appear without the evidence/explanation the spec pairs it with",
        "edge": "exact band boundaries on both sides of every cut-off; partial completion scored only on what was submitted, with no penalty where the spec forbids one; components the spec keeps separate must not be merged into the overall figure",
    },
    "ai_context": {
        "label": "AI context retention & personalization",
        "when": "context from an earlier step, session or document is reused by a later AI interaction",
        "requires": ("positive", "negative", "edge"),
        "negative": "when the context source is absent or skipped, the AI falls back to generic behaviour instead of referencing data that does not exist",
        "edge": "two context sources disagree — precedence must be deterministic and documented, not last-write-wins by accident; context must not leak across users, tenants or workspaces",
    },
    "ai_explainability": {
        "label": "AI explainability & reasoning output",
        "when": "the spec requires the AI to show why it produced a result",
        "requires": ("positive", "negative", "edge"),
        "negative": "a result rendered with its reasoning missing is a defect, not a cosmetic gap",
        "edge": "reasoning must trace to the actual input, not be boilerplate repeated verbatim across different subjects; a derived/composite result's reasoning must reflect the composition, not duplicate one component",
    },
    "media_capture": {
        "label": "Camera, microphone & voice capture",
        "when": "the flow depends on live audio, video or another device permission",
        "requires": ("positive", "negative", "edge"),
        "negative": "permission denied blocks continuation exactly as specified; the device disconnecting mid-flow produces the specified outcome, not an ambiguous hang",
        "edge": "degraded input (noise, low light, poor bitrate) degrades gracefully rather than failing falsely; a brief drop-and-recover is not treated as a hard failure unless the spec says so; captions/transcript state stays consistent with the audio state",
    },
}

# Categories whose behaviours are AI surfaces — used only for reporting, so a
# coverage scorecard can answer "did we probe the AI-specific risks at all?".
AI_CATEGORIES = frozenset(
    code for code in CATEGORIES if code.startswith("ai_") or code == "media_capture"
)

_FALLBACK_CATEGORY = "crud"


def category_requires(category: str | None) -> tuple[str, ...]:
    """Test types that MUST exist for a behaviour of this category."""
    entry = CATEGORIES.get(category or "")
    if not entry:
        return ("positive",)
    return tuple(entry["requires"])


def render_category_reference() -> str:
    """The category contract, rendered for the extraction prompt.

    Built from CATEGORIES rather than hand-written into the prompt string so
    the prompt and the code-side enforcement (check_variant_coverage) can
    never drift apart — a category added here is demanded by the prompt and
    enforced by the validator in the same commit.
    """
    lines: list[str] = []
    for code, entry in CATEGORIES.items():
        required = "/".join(entry["requires"])
        lines.append(f'- "{code}" — {entry["label"]}. Applies when {entry["when"]}. Required: {required}.')
        if entry.get("negative"):
            lines.append(f"    NEGATIVE probes: {entry['negative']}")
        if entry.get("edge"):
            lines.append(f"    EDGE probes: {entry['edge']}")
    return "\n".join(lines)


# ── Stage 0: zoning ──────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
# Numbered/underlined headings common in converted PDFs and DOCX exports:
# "4.3 Scope of Work", "SECTION 5 — ASSUMPTIONS".
_PLAIN_HEADING_RE = re.compile(
    r"^\s{0,3}((?:\d+\.)*\d+[.)]?\s+[A-Z][^.!?]{2,80}|[A-Z][A-Z0-9 &/,'\-]{4,80})\s*$"
)


def split_segments(text: str) -> list[dict]:
    """Split part text into heading-anchored segments.

    Returns [{heading, body, start, char_count}] in document order; the
    concatenation of every segment's body is the input text, so zoning can
    only ever *select* text, never rewrite it.

    Text before the first heading becomes a segment with heading=None — a
    part that begins mid-section (the chunker splits on real boundaries, but
    a hard_split part may not) still gets classified rather than discarded.
    """
    if not text.strip():
        return []

    lines = text.splitlines()
    segments: list[dict] = []
    current_heading: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer)
        if body.strip() or current_heading:
            segments.append({
                "heading": current_heading,
                "body": body,
                "char_count": len(body),
            })

    for line in lines:
        match = _HEADING_RE.match(line) or _PLAIN_HEADING_RE.match(line)
        if match:
            flush()
            groups = match.groups()
            current_heading = (groups[1] if len(groups) > 1 else groups[0]).strip()[:300]
            buffer = [line]
            continue
        buffer.append(line)

    flush()

    if not segments:
        return [{"heading": None, "body": text, "char_count": len(text)}]

    # Merge runt segments forward. A bare heading with no body, or a two-line
    # fragment, carries too little signal to classify on its own and would
    # only add noise (and cost) to the zoning call.
    merged: list[dict] = []
    for seg in segments:
        if merged and seg["char_count"] < _MIN_SEGMENT_CHARS:
            prev = merged[-1]
            prev["body"] = prev["body"] + "\n" + seg["body"]
            prev["char_count"] = len(prev["body"])
            continue
        merged.append(seg)
    return merged


def deterministic_zone_verdict(segment: dict) -> tuple[str, str] | None:
    """(zone_kind, reason) if this segment is non-testable on its heading
    alone, else None.

    Deterministic, free, and applied BEFORE any LLM call — a document that is
    mostly commercial boilerplate costs almost nothing to zone. The
    behaviour-marker veto keeps it conservative: a section titled
    "Assumptions" that nevertheless contains "the system must reject…" is
    handed to the LLM zoner rather than excluded outright, because a
    false exclusion loses a requirement and that is the one failure mode
    this whole module exists to avoid.
    """
    heading = (segment.get("heading") or "").strip()
    if not heading:
        return None
    for kind, pattern, reason in _NON_TESTABLE_RE:
        if pattern.search(heading):
            if _BEHAVIOUR_MARKER_RE.search(segment.get("body") or ""):
                return None
            return kind, reason
    return None


_ZONING_SYSTEM = (
    "You are a senior QA analyst deciding which parts of a requirements "
    "document can produce EXECUTABLE test cases. You are NOT extracting "
    "tests here — only deciding what is worth extracting from.\n"
    "\n"
    "You will receive numbered segments. Respond with JSON only:\n"
    '{"verdicts": [{"index": int, "testable": true|false, '
    '"zone_kind": str|null, "reason": str}]}\n'
    "\n"
    "A segment is TESTABLE when it describes something a tester could "
    "observe a running system doing: a user action and its result, a rule "
    "the system enforces, a value it computes, a state it stores, a message "
    "it shows, a permission it checks, an output it generates.\n"
    "\n"
    "A segment is NOT TESTABLE when it describes the PROJECT rather than the "
    "PRODUCT. Typical zone_kind values: \"commercial\" (pricing, payment "
    "terms), \"schedule\" (timelines, milestones, effort), \"resourcing\" "
    "(team, roles, responsibilities), \"assumptions\" (project assumptions "
    "and dependencies), \"out_of_scope\" (explicit exclusions — never test "
    "these), \"acceptance_process\" (sign-off and approval procedure), "
    "\"change_control\", \"legal\" (IP, liability, confidentiality), "
    "\"glossary\", \"doc_control\" (version history, contents), "
    "\"background\" (executive summary, business context, problem "
    "statement), \"support_terms\", \"methodology\" (ways of working), "
    "\"tooling\" (what it is built with, as opposed to what it must do).\n"
    "\n"
    "Judgement calls:\n"
    "- Aspiration is not behaviour. \"The platform will streamline hiring\" "
    "is background. \"A recruiter can shortlist a candidate from the list "
    "view\" is behaviour.\n"
    "- A non-functional target IS testable if it names an observable "
    "threshold (\"results load within 3 seconds\"); it is background if it "
    "is only a sentiment (\"the system will be fast and reliable\").\n"
    "- Mixed segments: if ANY part of the segment describes product "
    "behaviour, mark it testable. Extraction downstream can ignore the "
    "surrounding narrative; it cannot recover a segment you excluded.\n"
    "- When you are genuinely undecided, mark it TESTABLE. A wrongly "
    "included segment costs a few tokens and produces at most a weak "
    "checkpoint a human can delete. A wrongly excluded segment removes a "
    "requirement from testing permanently and invisibly.\n"
    "\n"
    "'reason' is one short sentence, always — for excluded segments it is "
    "shown to a human reviewer who needs to confirm nothing was lost."
)


def classify_zones(
    segments: list[dict], *, on_progress=None
) -> tuple[list[dict], list[dict], str]:
    """Split segments into (testable, excluded, model_used).

    Excluded entries carry {heading, zone_kind, reason, char_count,
    classifier} and are returned for storage — the caller writes them to
    SowPart.excluded_zones so a reviewer can audit exactly what the gate
    removed and why.

    Fails OPEN: any LLM problem, and every unclassified segment is treated as
    testable. Never raises.

    Reports its own progress rather than letting the caller infer it from the
    return value. The safety valve is why: when it fires, this returns
    excluded=[] — indistinguishable from "nothing needed excluding" — and a
    panel deriving the message from that would report a classifier
    malfunction as a clean pass. Only this function knows which happened.
    """
    from app.services.sow_progress import DONE, SKIPPED, report

    if not segments:
        return [], [], ""

    testable: list[dict] = []
    excluded: list[dict] = []
    undecided: list[tuple[int, dict]] = []

    for seg in segments:
        verdict = deterministic_zone_verdict(seg)
        if verdict is None:
            undecided.append((len(undecided), seg))
            continue
        kind, reason = verdict
        excluded.append({
            "heading": seg.get("heading"),
            "zone_kind": kind,
            "reason": reason,
            "char_count": seg["char_count"],
            "classifier": "deterministic",
        })

    model_used = ""
    if undecided and zoning_enabled():
        model_used = _llm_zone(undecided, testable, excluded)
    else:
        testable.extend(seg for _, seg in undecided)

    # Safety valve — see _ZONING_MAX_EXCLUSION_RATIO.
    total_chars = sum(s["char_count"] for s in segments) or 1
    excluded_chars = sum(int(e["char_count"]) for e in excluded)
    if excluded_chars / total_chars > _ZONING_MAX_EXCLUSION_RATIO:
        logger.error(
            "TDD zoning: verdict excluded %d/%d chars (%.0f%%) — above the %.0f%% "
            "alarm threshold, discarding the zoning result and extracting from the "
            "whole part instead",
            excluded_chars, total_chars,
            100 * excluded_chars / total_chars,
            100 * _ZONING_MAX_EXCLUSION_RATIO,
        )
        report(
            on_progress, "zoning", DONE,
            f"The testability check flagged almost the whole part "
            f"({100 * excluded_chars // total_chars}%) as non-testable, which is "
            "far more likely to be a misread than a document with no "
            "requirements — ignoring it and extracting from everything",
            {"discarded": True, "excluded_pct": 100 * excluded_chars // total_chars},
        )
        return list(segments), [], model_used

    logger.info(
        "TDD zoning: %d testable segment(s), %d excluded (%d/%d chars, %.0f%%) via %s",
        len(testable), len(excluded), excluded_chars, total_chars,
        100 * excluded_chars / total_chars, model_used or "deterministic rules only",
    )
    if not zoning_enabled():
        report(
            on_progress, "zoning", SKIPPED,
            "Skipped the testability check (TDD_ZONING is off) — extracting "
            "from the whole document",
        )
    elif excluded:
        kinds = sorted({str(e.get("zone_kind")) for e in excluded if e.get("zone_kind")})
        report(
            on_progress, "zoning", DONE,
            f"Set aside {len(excluded)} non-testable section"
            f"{'' if len(excluded) == 1 else 's'}"
            + (f" ({', '.join(kinds[:4]).replace('_', ' ')})" if kinds else ""),
            {"excluded": len(excluded), "kinds": kinds, "testable": len(testable)},
        )
    else:
        report(
            on_progress, "zoning", DONE,
            f"Checked {len(segments)} section"
            f"{'' if len(segments) == 1 else 's'} — all describe product behaviour",
            {"excluded": 0, "testable": len(testable)},
        )
    return testable, excluded, model_used


def _llm_zone(
    undecided: list[tuple[int, dict]],
    testable: list[dict],
    excluded: list[dict],
) -> str:
    """Run the LLM zoner over the segments the deterministic rules didn't
    settle, appending into `testable`/`excluded` in place. Returns the model
    label (empty string if every batch failed — in which case everything went
    to `testable`, the fail-open path)."""
    from app.services import llm_router

    models: list[str] = []
    for start in range(0, len(undecided), _MAX_SEGMENTS_PER_CALL):
        batch = undecided[start : start + _MAX_SEGMENTS_PER_CALL]
        payload = [
            {
                "index": i,
                "heading": seg.get("heading"),
                # Body preview only: zoning is a judgement about what KIND of
                # content this is, which the opening of a section settles.
                # Sending full bodies would cost as much as extraction itself.
                "preview": (seg.get("body") or "")[:1200],
                "char_count": seg["char_count"],
            }
            for i, seg in batch
        ]
        try:
            result = llm_router.complete_json_complete(
                "Classify these document segments:\n\n" + str(payload),
                system=_ZONING_SYSTEM,
                max_tokens=4096,
            )
        except Exception:  # noqa: BLE001 — zoning must never fail extraction
            logger.warning(
                "TDD zoning: batch at offset %d failed — its segments are treated as "
                "testable (fail-open)", start, exc_info=True,
            )
            testable.extend(seg for _, seg in batch)
            continue

        if result.model_used and result.model_used not in models:
            models.append(result.model_used)

        raw = result.parsed_json or {}
        entries = raw.get("verdicts", []) if isinstance(raw, dict) else []
        verdict_by_index: dict[int, dict] = {}
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("index"), int):
                verdict_by_index[entry["index"]] = entry

        for i, seg in batch:
            entry = verdict_by_index.get(i)
            # No verdict for this segment => testable. An LLM that returns a
            # short list must not thereby delete the segments it skipped.
            if entry is None or entry.get("testable") is not False:
                testable.append(seg)
                continue
            excluded.append({
                "heading": seg.get("heading"),
                "zone_kind": str(entry.get("zone_kind") or "unclassified")[:60],
                "reason": str(entry.get("reason") or "classified as non-testable project content")[
                    :_MAX_ZONE_REASON_CHARS
                ],
                "char_count": seg["char_count"],
                "classifier": "llm",
            })

    return ", ".join(models)


# ── Stages 1-3: behaviour extraction with mandatory variants ─────────────────

_EXTRACTION_RESPONSE_SHAPE = (
    '{"behaviours": [{\n'
    '  "behaviour_key": str,      // stable kebab-case id, e.g. "job-create"\n'
    '  "title": str,              // 3-6 words, e.g. "Create Job"\n'
    '  "category": str,           // one code from the list below\n'
    '  "page": str|null,\n'
    '  "checkpoints": [{\n'
    '     "type": "functional"|"visual",\n'
    '     "test_type": "positive"|"negative"|"edge",\n'
    '     "grounding": "stated"|"derived",\n'
    '     "priority": "smoke"|"sanity"|"regression",\n'
    '     "title": str,\n'
    '     "description": str|null,   // visual only\n'
    '     "role": str|null, "objective": str|null, "context": str|null,\n'
    '     "instructions": [str]|null, "notes": [str]|null,\n'
    '     "expected": str|null,\n'
    '     "review_status": "ready"|"needs_review"|"needs_design_flow",\n'
    '     "review_reason": str|null\n'
    "  }]\n"
    "}]}"
)


def build_extraction_system(
    part_label: str | None = None, ui_inventory: str | None = None
) -> str:
    """The Stage 1-3 system prompt.

    Assembled at call time from CATEGORIES so the contract the model is held
    to is literally the same object check_variant_coverage() enforces.
    """
    prompt = (
        "You are a senior QA engineer converting a requirements document into "
        "EXECUTABLE test definitions (TDDs) for a browser-automation agent.\n"
        "\n"
        "Respond with JSON only:\n"
        f"{_EXTRACTION_RESPONSE_SHAPE}\n"
        "\n"
        "══ RULE 1 — EXTRACT BEHAVIOURS, NOT TEXT ══\n"
        "You are not summarising the document. You are finding BEHAVIOURS. A "
        "behaviour is a tuple: an actor does something, the system responds "
        "observably, under a rule. If you cannot name all three, it is not a "
        "behaviour and it does not belong in your output.\n"
        "\n"
        "Produce NOTHING for text that describes the project rather than the "
        "product: scope statements, deliverable lists, timelines, effort, "
        "pricing, team structure, assumptions, dependencies, out-of-scope "
        "items, sign-off procedure, legal terms, glossary, document history, "
        "background and business rationale, technology choices. Returning "
        "zero behaviours for such a passage is the CORRECT answer. Do not "
        "manufacture a checkpoint to avoid an empty result.\n"
        "\n"
        "Never emit a checkpoint whose objective is to 'verify the document "
        "states…', 'confirm the scope includes…', 'check that the platform "
        "supports…' in the abstract, or to review a deliverable. Those are "
        "document-reading tasks, not product tests.\n"
        "\n"
        "══ RULE 2 — EVERY BEHAVIOUR GETS VARIANTS ══\n"
        "One behaviour produces SEVERAL checkpoints, not one. A happy path "
        "alone is not test coverage — it proves the feature works when "
        "nothing goes wrong, which is the case that was already manually "
        "checked during development.\n"
        "\n"
        "  positive — the intended path succeeds and the stated outcome is "
        "observable.\n"
        "  negative — the system is given something it must REFUSE, or a "
        "dependency fails. Pass means it refuses/degrades correctly and "
        "safely: a visible, specific message, no partial write, no elevated "
        "access, no silent success.\n"
        "  edge     — a boundary, an empty/maximum value, a concurrent or "
        "interrupted action, an unusual-but-legal input. Pass means defined, "
        "documented behaviour rather than an accident.\n"
        "\n"
        "Assign each behaviour ONE 'category' from the list at the end of "
        "this prompt, then emit AT MINIMUM the test types that category "
        "marks as Required, using its NEGATIVE and EDGE probes as your "
        "starting point. Skipping a required variant is the single most "
        "common failure of this task — check your output against the "
        "category's Required list before you finish.\n"
        "\n"
        "══ RULE 3 — GROUNDING: SAY WHERE THE EXPECTATION CAME FROM ══\n"
        "  grounding=\"stated\"  — the document explicitly specifies this "
        "expectation.\n"
        "  grounding=\"derived\" — YOU inferred it from standard QA practice "
        "because the document is silent.\n"
        "Most negative and edge checkpoints are \"derived\", and that is "
        "correct and wanted — a document rarely enumerates its own failure "
        "modes. But a derived checkpoint must assert a GENERIC safe outcome "
        "(\"the submission is rejected with a visible error and no record is "
        "created\"), never a specific unstated detail (never invent an exact "
        "error string, an exact limit, or an exact status name the document "
        "does not give). When the document IS silent about the specific "
        "value, say so in 'notes' rather than guessing it into "
        "'instructions'.\n"
        "\n"
        "══ RULE 4 — RUNNABLE, ATOMIC, INDEPENDENT ══\n"
        "A browser-automation agent executes each checkpoint alone, with no "
        "other context. For every functional checkpoint fill:\n"
        "  'role'        persona and preconditions, e.g. \"Logged in as an "
        "admin with permission to create jobs.\"\n"
        "  'objective'   ONE sentence defining PASS, stated as an observable "
        "outcome. For a negative checkpoint, PASS is the refusal — write "
        "\"the form is rejected and no job is created\", never \"the form "
        "fails\".\n"
        "  'context'     the starting page/state.\n"
        "  'instructions' ordered, atomic, imperative steps — one action per "
        "string, ending with the verification step. Name the actual controls "
        "and fields. Use plausible example values where the document gives "
        "none, and note in 'notes' that the value was chosen, not "
        "specified.\n"
        "  'notes'       caveats, exact expected values, and any assumption "
        "you had to make.\n"
        "\n"
        "'title' on a checkpoint states the case, e.g. \"Create Job — "
        "required field empty\". 'behaviour_key' is a stable kebab-case id "
        "shared by every variant of the same behaviour; keep it identical "
        "across re-analysis of the same document.\n"
        "\n"
        "══ RULE 5 — HONESTY OVER COMPLETENESS-THEATRE ══\n"
        "If the document names a control or screen without saying what it "
        "does, still emit the behaviour, set review_status=\"needs_review\" "
        "and put exactly what is missing in 'review_reason'. If it implies a "
        "flow it never describes at all, use \"needs_design_flow\". Use "
        "\"ready\" ONLY when every step you wrote is grounded — in the "
        "document for a \"stated\" checkpoint, or in the category's standard "
        "probes for a \"derived\" one. Never fabricate steps to make a "
        "requirement look complete.\n"
        "\n"
        "══ CATEGORY CONTRACT ══\n"
        "Pick the ONE category that best fits the behaviour. A behaviour that "
        "touches an AI/model surface takes the ai_* category over the generic "
        "one — the AI risks are the ones nothing else will catch.\n"
        f"{render_category_reference()}\n"
        "\n"
        'Return {"behaviours": []} when the text contains no product '
        "behaviour at all. That is a valid, expected answer."
    )
    if part_label:
        prompt += (
            f"\n\nNOTE: this is {part_label} of a larger document. Analyse only "
            "what appears in this excerpt; missing context elsewhere in the "
            "document does not mean the requirement is absent, so do not flag "
            "a requirement as incomplete merely because its surrounding "
            "sections are not shown here."
        )
    if ui_inventory:
        # Appended LAST, after the category contract and the part note, so the
        # naming reference reads as a lookup table applied to the rules above
        # rather than as another rule competing with them.
        from app.services.ui_inventory import format_for_prompt

        prompt += format_for_prompt(ui_inventory)
    return prompt


def extract_behaviours(
    text: str, *, part_label: str | None = None, ui_inventory: str | None = None
) -> tuple[list[dict], str]:
    """Stage 1-3. Returns (flat checkpoint list, model_used).

    Behaviour-level fields (behaviour_key, category, page) are pushed down
    onto each of that behaviour's checkpoints, so the rest of the pipeline —
    validation, dedupe, skill creation — keeps working on one flat list
    exactly as it did before.

    Raises IngestError on total provider failure, matching parse_sow's
    contract so the worker's existing error handling is unchanged.
    """
    from app.services import design_ingest, llm_router

    prompt = "Extract executable test definitions from this requirements text:\n\n" + text
    try:
        result = design_ingest._complete_via_brain(
            prompt,
            system=build_extraction_system(part_label, ui_inventory),
            max_tokens=8192,
        )
    except llm_router.LLMRouterError as exc:
        raise IngestError(f"All LLM providers failed: {exc}") from exc

    raw = result.parsed_json or {}
    behaviours = raw.get("behaviours", []) if isinstance(raw, dict) else []

    checkpoints: list[dict] = []
    seen_raw = 0
    for behaviour in behaviours:
        if not isinstance(behaviour, dict):
            continue
        key = _normalize_key(behaviour.get("behaviour_key") or behaviour.get("title") or "")
        category = _normalize_category(behaviour.get("category"))
        page = behaviour.get("page")
        items = behaviour.get("checkpoints")
        if not isinstance(items, list):
            continue

        produced: list[dict] = []
        for item in items:
            seen_raw += 1
            if not isinstance(item, dict):
                continue
            item.setdefault("page", page)
            item["behaviour_key"] = key
            item["category"] = category
            validated = design_ingest.validate_checkpoint(item)
            if validated:
                produced.append(validated)

        produced = check_variant_coverage(produced, category=category, behaviour_key=key)
        # Cap after the coverage check, never before: dropping a required
        # variant would be flagged as a gap, Stage 4b would re-request it, and
        # this would drop it again.
        produced = cap_variants(produced, behaviour_key=key)
        checkpoints.extend(produced)

    dropped = seen_raw - len(checkpoints)
    if dropped > 0:
        logger.warning("TDD extraction: dropped %d schema-invalid checkpoint(s)", dropped)
    logger.info(
        "TDD extraction: %d behaviour(s) -> %d checkpoint(s) via %s",
        len(behaviours), len(checkpoints), result.model_used,
    )
    return checkpoints, result.model_used


def _normalize_key(value: object) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return slug[:_MAX_BEHAVIOR_KEY_CHARS] or "unkeyed-behaviour"


def _normalize_category(value: object) -> str:
    code = str(value or "").strip().lower()
    return code if code in CATEGORIES else _FALLBACK_CATEGORY


# ── Stage 4: deterministic coverage backstop ─────────────────────────────────

def check_variant_coverage(
    checkpoints: list[dict], *, category: str, behaviour_key: str
) -> list[dict]:
    """Enforce the category's Required test types in CODE, not in the prompt.

    The model is asked for the required variants; this verifies it actually
    produced them. A behaviour missing a required variant is NOT dropped and
    NOT silently accepted — its positive checkpoint carries a
    coverage_gap list naming what is missing, which the scorecard counts and
    the UI can surface. Same principle as design_ingest's
    "flag on the evidence, don't trust the model's own claim".

    Returns the (possibly annotated) checkpoints unchanged in order.
    """
    if not checkpoints:
        return checkpoints

    required = set(category_requires(category))
    present = {cp.get("test_type") for cp in checkpoints}
    missing = sorted(required - present)
    if not missing:
        return checkpoints

    logger.info(
        "TDD extraction: behaviour %r (%s) is missing required variant(s): %s",
        behaviour_key, category, ", ".join(missing),
    )
    anchor = next(
        (cp for cp in checkpoints if cp.get("test_type") == "positive"), checkpoints[0]
    )
    anchor["coverage_gap"] = missing
    return checkpoints


def apply_variant_backstop(checkpoints: list[dict]) -> list[dict]:
    """Run Stage 4 (coverage check) and Stage 4c (cap) over a FLAT list.

    extract_behaviours applies both per behaviour as it builds them, because
    it has the behaviours in hand. Anything that arrives as an already-flat
    list — the video path, which digests observed checkpoints first and
    categorises them afterwards — needs the same treatment applied by
    grouping on behaviour_key.

    Without this, Stages 4 and 4c were silently SOW-only: a walkthrough-
    derived behaviour missing a variant its category requires was neither
    flagged nor repaired, and a verbose one was unbounded. Two sources
    feeding one Skills table with two different levels of rigour is exactly
    the kind of difference nobody remembers when reading the results.

    Checkpoints with no behaviour_key (visual checkpoints, anything from the
    legacy path) pass through untouched rather than being forced into a
    group — they have no category contract to enforce.
    """
    if not checkpoints:
        return checkpoints

    groups: dict[str, list[dict]] = {}
    for cp in checkpoints:
        key = str(cp.get("behaviour_key") or "")
        if key and cp.get("type") == "functional":
            groups.setdefault(key, []).append(cp)

    processed: dict[str, list[dict]] = {}
    for key, members in groups.items():
        category = _FALLBACK_CATEGORY
        for cp in members:
            if cp.get("category"):
                category = _normalize_category(cp.get("category"))
                break
        checked = check_variant_coverage(members, category=category, behaviour_key=key)
        processed[key] = cap_variants(checked, behaviour_key=key)

    # Rebuild in place: a group's survivors are emitted at the position of its
    # FIRST member, so ordering is preserved and a behaviour's variants stay
    # together even when the cap removed some of them.
    out: list[dict] = []
    emitted: set[str] = set()
    for cp in checkpoints:
        key = str(cp.get("behaviour_key") or "")
        if key in processed:
            if key not in emitted:
                out.extend(processed[key])
                emitted.add(key)
            continue
        out.append(cp)
    return out


# ── Stage 4c: variant volume cap ─────────────────────────────────────────────
#
# A rich behaviour legitimately produces many variants — an input_validation
# rule with several fields has a distinct negative case per field, and all of
# them are real tests. Nothing bounded that. The only thing standing between a
# verbose behaviour and an unbounded skill list was the extraction call's
# max_tokens, which truncates the model's JSON at whatever character it
# happens to reach: the tests you lose are chosen by accident, the loss is
# invisible, and it lands in the middle of an array so it can take a
# well-formed checkpoint with it.
#
# This is the same failure mode _FACTS_PER_DRAFT_CALL was introduced to fix in
# the drafting pipeline, and it gets the same answer: bound the work
# deliberately and say what was dropped, rather than letting a token limit
# make the choice silently.
#
# The selection rule, in order:
#
#   1. Keep one checkpoint of EVERY test type present, before anything else.
#      Dropping the only edge case to keep a fourth negative would gut the
#      negative_edge_ratio and remove a category's required coverage — the
#      opposite of what this pipeline exists to do.
#   2. Fill the remaining slots by priority (smoke > sanity > regression),
#      breaking ties on document order so the result is stable across
#      re-analysis of the same part.
#   3. Restore document order for whatever survived, so the reader sees the
#      behaviour's variants in the order the document implies rather than in
#      priority order.

# Ceiling per BEHAVIOUR, not per part or per document. The unit matters: a
# document with fifty modest behaviours is fine and must not be trimmed, while
# one behaviour with twenty variants is where the runaway actually happens.
_MAX_VARIANTS_PER_BEHAVIOUR = 8


def _priority_rank(cp: dict) -> int:
    """Lower sorts first. An unrecognised priority sorts last rather than
    raising — it is the conservative reading, matching DEFAULT_PRIORITY."""
    priority = str(cp.get("priority") or DEFAULT_PRIORITY).strip().lower()
    try:
        return PRIORITIES.index(priority)
    except ValueError:
        return len(PRIORITIES)


def cap_variants(checkpoints: list[dict], *, behaviour_key: str = "") -> list[dict]:
    """Bound one behaviour's variant count by priority. Returns the survivors
    in document order.

    Applied AFTER check_variant_coverage so a required variant is never
    dropped and then immediately re-flagged as a gap (which Stage 4b would
    then re-request, and this would drop again — a loop that spends tokens
    forever and converges on nothing).
    """
    if not variant_cap_enabled() or len(checkpoints) <= _MAX_VARIANTS_PER_BEHAVIOUR:
        return checkpoints

    indexed = list(enumerate(checkpoints))
    kept_indices: set[int] = set()

    # Rule 1 — one of every test type present, best priority first.
    for test_type in TEST_TYPES:
        of_type = [(i, cp) for i, cp in indexed if cp.get("test_type") == test_type]
        if of_type:
            kept_indices.add(min(of_type, key=lambda pair: (_priority_rank(pair[1]), pair[0]))[0])

    # Rule 2 — fill the rest by priority, then document order.
    for i, _cp in sorted(indexed, key=lambda pair: (_priority_rank(pair[1]), pair[0])):
        if len(kept_indices) >= _MAX_VARIANTS_PER_BEHAVIOUR:
            break
        kept_indices.add(i)

    dropped = [cp for i, cp in indexed if i not in kept_indices]
    # Rule 3 — document order for the survivors.
    survivors = [cp for i, cp in indexed if i in kept_indices]

    # No silent caps: the reader has to be able to tell a behaviour that
    # produced eight tests from one that produced twenty and kept eight.
    logger.warning(
        "TDD cap: behaviour %r produced %d variants, keeping %d by priority — "
        "dropped: %s",
        behaviour_key or "?", len(checkpoints), len(survivors),
        "; ".join(
            f"{cp.get('test_type') or '?'}/{cp.get('priority') or DEFAULT_PRIORITY}: "
            f"{cp.get('title') or cp.get('objective') or 'untitled'}"
            for cp in dropped
        ),
    )
    anchor = next(
        (cp for cp in survivors if cp.get("test_type") == "positive"), survivors[0]
    )
    anchor["capped_variants"] = len(dropped)
    return survivors


# ── Stage 4b: coverage-gap repair ────────────────────────────────────────────
#
# Stage 4 detects that a behaviour is missing a variant its category requires
# and records it. On its own that is a sticky note saying you don't have the
# test — the hole still ships.
#
# This stage closes it. It re-asks for ONLY the missing variants, which is a
# much narrower question than the original extraction: the behaviour, its
# category and its happy path are already known and supplied, so the model is
# writing one specific test rather than deciding what is a requirement, how to
# categorise it, and how to phrase five things at once. That is exactly the
# job-splitting argument the whole module is built on (spec §1.3), applied one
# level down.
#
# Three properties this stage must have, in order of importance:
#
#   1. It never claims success it did not achieve. Coverage is recomputed from
#      the repaired checkpoints, not from the model's reply — the same
#      "trust evidence, not the model's claim" rule Stage 4 exists to enforce
#      (P5). A behaviour whose gap could not be filled keeps its coverage_gap.
#   2. It never fails a parse. Every failure path returns the input unchanged;
#      losing a whole part's real checkpoints to a failed enrichment call
#      would be far worse than shipping the gap the flag already documents.
#   3. Everything it produces is grounding="derived". A repaired variant is
#      reasoned from QA practice, not read out of the document, and labelling
#      it "stated" would make a spec gap indistinguishable from a real defect
#      during triage (P6).

_REPAIR_SYSTEM_HEAD = (
    "You are a senior QA engineer completing test coverage for behaviours "
    "that have already been extracted from a requirements document.\n"
    "\n"
    "Each behaviour below comes with the test cases that were ALREADY "
    "written for it, and a list of the test types that are MISSING. Write "
    "ONLY the missing ones.\n"
    "\n"
    "Rules:\n"
    "  1. Do not rewrite, reword or duplicate the existing cases. They are "
    "settled.\n"
    "  2. Write a separate, independently runnable test for each missing "
    "type — never a note or a caveat appended to the existing case.\n"
    "  3. Reuse the exact screen, control and field labels that appear in "
    "the existing case's instructions. Those came from the document; "
    "inventing new ones produces a test that cannot be executed.\n"
    "  4. For a NEGATIVE test, PASS is the refusal. Write the expected "
    "outcome as the refusal itself — \"the form is rejected with a visible "
    "error and no job is created\" — never \"the form fails\".\n"
    "  5. Assert only a GENERIC safe outcome. The document did not state "
    "these expectations, so you must NOT invent a specific unstated detail: "
    "no exact error string, no exact numeric limit, no exact status name. "
    "Where the document is silent on such a value, say so in 'notes' — "
    "never put a guessed value in 'instructions'.\n"
    "  6. Instructions are atomic: one action per step, ending with the "
    "verification step.\n"
    "  7. If a missing variant genuinely cannot be written for a behaviour "
    "without inventing product behaviour, omit it. Returning fewer tests is "
    "correct; a fabricated test is not.\n"
    "\n"
    "Respond with JSON only:\n"
    '{"repairs": [{"behaviour_key": str, '
    '"test_type": "negative"|"edge", "title": str, "role": str|null, '
    '"objective": str, "context": str|null, "instructions": [str], '
    '"notes": [str], "priority": "smoke"|"sanity"|"regression"}]}\n'
    "\n"
    "'behaviour_key' must be copied verbatim from the behaviour you are "
    "completing. The probes each category requires are listed below — use "
    "them to decide what the missing test should attempt.\n"
    "\n"
    "══ CATEGORY CONTRACT ══\n"
)

# Per-part ceiling on how many behaviours one repair call covers. A part with
# more gaps than this has a systemic extraction problem that another LLM call
# will not fix (spec §10: coverage_gaps above ~10% of behaviours means
# investigate the prompt or the provider, not the document). Anything beyond
# the cap keeps its coverage_gap flag and is named in the log — a silent cap
# would read as "everything was repaired" when it was not.
_MAX_REPAIR_BEHAVIOURS = 12

# How much of the existing happy path to send per behaviour. Enough for the
# model to reuse the real labels; not so much that repairing a dozen
# behaviours costs as much as the extraction call it is patching.
_MAX_REPAIR_INSTRUCTIONS = 8


def _gap_anchors(checkpoints: list[dict]) -> list[dict]:
    """The checkpoints carrying an unfilled coverage_gap, in document order."""
    return [cp for cp in checkpoints if cp.get("coverage_gap")]


def repair_coverage_gaps(
    checkpoints: list[dict],
    *,
    part_label: str | None = None,
    ui_inventory: str | None = None,
) -> tuple[list[dict], str]:
    """Stage 4b. Re-ask for the variants Stage 4 found missing.

    Returns (checkpoints, model_used). model_used is "" when no call was made
    (nothing to repair, flag off, or v2 disabled) or when the call failed.

    Never raises: repair is enrichment, and a part whose extraction succeeded
    must not be failed by it.
    """
    if not checkpoints or not v2_enabled() or not gap_repair_enabled():
        return checkpoints, ""

    anchors = _gap_anchors(checkpoints)
    if not anchors:
        return checkpoints, ""

    targeted, deferred = anchors[:_MAX_REPAIR_BEHAVIOURS], anchors[_MAX_REPAIR_BEHAVIOURS:]
    if deferred:
        # Named, not swallowed: the reader has to be able to tell "repaired
        # everything" from "repaired the first twelve".
        logger.warning(
            "TDD repair: %d behaviour(s) had coverage gaps but only %d are "
            "repaired per part; these keep their gap flag: %s",
            len(anchors), _MAX_REPAIR_BEHAVIOURS,
            ", ".join(str(cp.get("behaviour_key") or "?") for cp in deferred),
        )

    by_key: dict[str, list[dict]] = {}
    for cp in checkpoints:
        by_key.setdefault(str(cp.get("behaviour_key") or ""), []).append(cp)

    payload = []
    for anchor in targeted:
        key = str(anchor.get("behaviour_key") or "")
        category = _normalize_category(anchor.get("category"))
        contract = CATEGORIES.get(category, {})
        payload.append({
            "behaviour_key": key,
            "category": category,
            "missing_test_types": list(anchor.get("coverage_gap") or []),
            "what_to_probe": {
                "negative": contract.get("negative") or "",
                "edge": contract.get("edge") or "",
            },
            "existing_cases": [
                {
                    "test_type": cp.get("test_type"),
                    "title": cp.get("title"),
                    "objective": cp.get("objective"),
                    "context": cp.get("context"),
                    "instructions": (cp.get("instructions") or [])[:_MAX_REPAIR_INSTRUCTIONS],
                }
                for cp in by_key.get(key, [])
            ],
        })

    prompt = "Write the missing test cases for these behaviours:\n\n" + str(payload)
    if part_label:
        prompt += (
            f"\n\nThese behaviours come from {part_label} of a larger document. "
            "Do not assume a missing detail is absent from the product — say so "
            "in 'notes' instead of inventing it."
        )

    try:
        from app.services import design_ingest

        # The repair call writes instructions too, so it needs the same
        # naming reference — a repaired negative case that clicks a button
        # the product does not have is exactly as useless as an extracted one.
        system = _REPAIR_SYSTEM_HEAD + render_category_reference()
        if ui_inventory:
            from app.services.ui_inventory import format_for_prompt

            system += format_for_prompt(ui_inventory)

        result = design_ingest._complete_via_brain(
            prompt, system=system, max_tokens=8192
        )
    except Exception:  # noqa: BLE001 — repair must never fail a successful parse
        logger.warning(
            "TDD repair: coverage-gap repair call failed — %d behaviour(s) keep "
            "their coverage_gap flag and no checkpoints were lost",
            len(targeted), exc_info=True,
        )
        return checkpoints, ""

    raw = result.parsed_json or {}
    repairs = raw.get("repairs", []) if isinstance(raw, dict) else []

    anchor_by_key = {str(cp.get("behaviour_key") or ""): cp for cp in targeted}
    repaired_by_key: dict[str, list[dict]] = {}
    for item in repairs:
        if not isinstance(item, dict):
            continue
        key = _normalize_key(item.get("behaviour_key") or "")
        anchor = anchor_by_key.get(key)
        if anchor is None:
            # A behaviour_key we did not ask about. Dropped rather than
            # appended: it would be an unrequested test with no behaviour to
            # belong to, and silently widening the output is how the original
            # "everything becomes a TDD" defect behaved.
            continue
        test_type = str(item.get("test_type") or "").strip().lower()
        if test_type not in (anchor.get("coverage_gap") or []):
            # Only the types actually missing. The model re-supplying a
            # variant that already exists must not create a duplicate.
            continue
        candidate = {
            **item,
            "type": "functional",
            "test_type": test_type,
            "page": anchor.get("page"),
            "category": anchor.get("category"),
            "behaviour_key": key,
            # Reasoned from QA practice, not read out of the document —
            # always, regardless of what the model claims.
            "grounding": "derived",
        }
        validated = design_ingest.validate_checkpoint(candidate)
        if validated:
            repaired_by_key.setdefault(key, []).append(validated)

    if not repaired_by_key:
        logger.info(
            "TDD repair: the model returned no usable variants for %d behaviour(s) "
            "— coverage_gap flags left in place",
            len(targeted),
        )
        return checkpoints, result.model_used

    # Splice each behaviour's repairs in after its existing checkpoints so a
    # behaviour's variants stay together and document order is preserved.
    merged: list[dict] = []
    emitted: set[str] = set()
    for cp in checkpoints:
        merged.append(cp)
        key = str(cp.get("behaviour_key") or "")
        if key in repaired_by_key and key not in emitted:
            last_index = max(
                i for i, other in enumerate(checkpoints)
                if str(other.get("behaviour_key") or "") == key
            )
            if checkpoints[last_index] is cp:
                merged.extend(repaired_by_key[key])
                emitted.add(key)

    # Re-derive the gap from the RESULT, never from the reply. A variant the
    # model claimed but that failed validation, or that it quietly skipped,
    # must leave the flag standing.
    filled = 0
    for key, anchor in anchor_by_key.items():
        still_missing = sorted(
            set(anchor.get("coverage_gap") or [])
            - {cp.get("test_type") for cp in repaired_by_key.get(key, [])}
        )
        if still_missing:
            anchor["coverage_gap"] = still_missing
        else:
            anchor.pop("coverage_gap", None)
            filled += 1

    logger.info(
        "TDD repair: %d/%d behaviour(s) fully repaired, %d variant(s) added via %s",
        filled, len(targeted),
        sum(len(v) for v in repaired_by_key.values()), result.model_used,
    )
    return merged, result.model_used


# ── Variant expansion for already-observed checkpoints (video path) ──────────
#
# The video digester (app.services.video_ingest) works under a deliberately
# strict grounding rule: describe ONLY what the recording actually shows. That
# rule is correct and must not be relaxed — a video is evidence, and inventing
# behaviour from it is how a walkthrough turns into fiction. But it also means
# a video can only ever produce happy paths, because a demo only demonstrates
# things working.
#
# So variants are added here instead, as a SEPARATE, explicitly-labelled pass:
# every checkpoint it produces is grounding="derived", which is the honest
# description of a negative case reasoned from an observed positive one. The
# observed checkpoints themselves are never rewritten — only categorised.

_EXPANSION_SYSTEM_HEAD = (
    "You are a senior QA engineer. You will be given a list of VERIFIED "
    "positive test cases that were observed directly in a product "
    "walkthrough recording. They are evidence: do not rewrite them, do not "
    "reword them, do not question them.\n"
    "\n"
    "Your job is two things:\n"
    "  1. Assign each observed case ONE behaviour category from the contract "
    "below.\n"
    "  2. Derive the NEGATIVE and EDGE cases that category requires but the "
    "recording could not show — a demo only ever demonstrates success.\n"
    "\n"
    "Respond with JSON only:\n"
    '{"assignments": [{"index": int, "category": str, '
    '"behaviour_key": str}],\n'
    ' "derived": [{"index": int, "test_type": "negative"|"edge", '
    '"title": str, "role": str|null, "objective": str, "context": str|null, '
    '"instructions": [str], "notes": [str], '
    '"priority": "smoke"|"sanity"|"regression"}]}\n'
    "\n"
    "'index' refers to the observed case a derived test belongs to; reuse "
    "that case's behaviour_key and category.\n"
    "\n"
    "Derived tests assert a GENERIC safe outcome — \"the submission is "
    "rejected with a visible error and no record is created\" — never an "
    "exact error string, limit or status name, because the recording never "
    "showed one. State any such assumption in 'notes'. Reuse the exact "
    "control and field labels from the observed case's instructions; those "
    "were read off the screen and are the only labels known to be real.\n"
    "\n"
    "══ CATEGORY CONTRACT ══\n"
)


def classify_and_expand(checkpoints: list[dict]) -> tuple[list[dict], str]:
    """Categorise observed checkpoints and append their derived variants.

    Returns (checkpoints_with_variants, model_used). Never raises and never
    loses an input checkpoint: on any failure the input is returned with the
    fallback category applied, because a video that digested successfully
    must not be failed by an enrichment pass.
    """
    from app.services import design_ingest, llm_router

    functional = [
        (i, cp) for i, cp in enumerate(checkpoints)
        if cp.get("type") == "functional" and cp.get("objective")
    ]
    if not functional or not v2_enabled():
        return checkpoints, ""

    payload = [
        {
            "index": i,
            "title": cp.get("title"),
            "objective": cp.get("objective"),
            "context": cp.get("context"),
            "instructions": cp.get("instructions") or [],
        }
        for i, cp in functional
    ]

    try:
        result = llm_router.complete_json_complete(
            "Categorise and expand these observed test cases:\n\n" + str(payload),
            system=_EXPANSION_SYSTEM_HEAD + render_category_reference(),
            max_tokens=8192,
        )
    except Exception:  # noqa: BLE001 — enrichment must never fail digestion
        logger.warning(
            "TDD expansion: variant derivation failed — observed checkpoints kept "
            "as-is with no negative/edge coverage", exc_info=True,
        )
        return checkpoints, ""

    raw = result.parsed_json or {}
    assignments = raw.get("assignments", []) if isinstance(raw, dict) else []
    derived_items = raw.get("derived", []) if isinstance(raw, dict) else []

    meta_by_index: dict[int, tuple[str, str]] = {}
    for entry in assignments:
        if not isinstance(entry, dict) or not isinstance(entry.get("index"), int):
            continue
        meta_by_index[entry["index"]] = (
            _normalize_category(entry.get("category")),
            _normalize_key(entry.get("behaviour_key") or ""),
        )

    # Apply categories in place. An unassigned checkpoint keeps the fallback
    # category rather than being skipped.
    for i, cp in functional:
        category, key = meta_by_index.get(
            i, (_FALLBACK_CATEGORY, _normalize_key(cp.get("title") or ""))
        )
        cp["category"] = category
        cp["behaviour_key"] = key
        cp["test_type"] = "positive"
        cp["grounding"] = "stated"  # it was observed on screen — the strongest grounding there is

    derived: list[dict] = []
    for item in derived_items:
        if not isinstance(item, dict):
            continue
        parent_index = item.get("index")
        parent = dict(checkpoints[parent_index]) if isinstance(parent_index, int) and 0 <= parent_index < len(checkpoints) else None
        if parent is None or parent.get("type") != "functional":
            continue
        candidate = {
            **item,
            "type": "functional",
            "page": parent.get("page"),
            "category": parent.get("category"),
            "behaviour_key": parent.get("behaviour_key"),
            "grounding": "derived",
        }
        validated = design_ingest.validate_checkpoint(candidate)
        if validated:
            derived.append(validated)

    combined = dedupe(list(checkpoints) + derived)
    # The same backstop the SOW path gets. The expansion prompt ASKS for the
    # variants each category requires; this checks in code that they arrived
    # (P5), re-asks for the ones that did not, and bounds a behaviour that
    # produced too many. Without it Stages 4/4b/4c were SOW-only and a
    # walkthrough-derived behaviour was held to a visibly lower standard than
    # a document-derived one.
    combined = apply_variant_backstop(combined)
    combined, repair_model = repair_coverage_gaps(combined)

    models = [m for m in (result.model_used, repair_model) if m]
    model_used = ", ".join(dict.fromkeys(models))
    logger.info(
        "TDD expansion: %d observed checkpoint(s) categorised, %d derived "
        "negative/edge case(s) added, %d checkpoint(s) after the coverage "
        "backstop via %s",
        len(functional), len(derived), len(combined), model_used,
    )
    return combined, model_used


# ── Stage 5: dedupe + scorecard ──────────────────────────────────────────────

def _dedupe_signature(cp: dict) -> tuple[str, str, str]:
    objective = " ".join(str(cp.get("objective") or cp.get("description") or "").lower().split())
    return (
        str(cp.get("behaviour_key") or ""),
        str(cp.get("test_type") or ""),
        objective[:200],
    )


def dedupe(checkpoints: list[dict]) -> list[dict]:
    """Drop exact repeats of the same (behaviour, test type, objective).

    Deliberately narrow. Two checkpoints for the same behaviour and test type
    with DIFFERENT objectives are two different cases (an input form has many
    distinct negative cases) and both are kept. Only a literal repeat — the
    same behaviour restated in an overview section and again in a detail
    section — collapses. First occurrence wins, so document order is stable.
    """
    seen: set[tuple[str, str, str]] = set()
    kept: list[dict] = []
    for cp in checkpoints:
        signature = _dedupe_signature(cp)
        if signature in seen:
            continue
        seen.add(signature)
        kept.append(cp)
    dropped = len(checkpoints) - len(kept)
    if dropped:
        logger.info("TDD extraction: deduped %d duplicate checkpoint(s)", dropped)
    return kept


def scorecard(checkpoints: list[dict], excluded_zones: list[dict] | None = None) -> dict:
    """Coverage metrics for one part or one whole document.

    This is the acceptance gate for the extractor itself. The headline number
    is negative_edge_ratio: on the old pipeline it was ~0 by construction,
    which is precisely the defect this module fixes. Stored on the part so a
    regression in extraction quality is visible without re-reading every
    generated skill.
    """
    by_type: dict[str, int] = {t: 0 for t in TEST_TYPES}
    by_category: dict[str, int] = {}
    by_grounding: dict[str, int] = {g: 0 for g in GROUNDINGS}
    flagged = 0
    gaps: list[dict] = []
    capped = 0

    for cp in checkpoints:
        by_type[cp.get("test_type") or DEFAULT_TEST_TYPE] = (
            by_type.get(cp.get("test_type") or DEFAULT_TEST_TYPE, 0) + 1
        )
        category = cp.get("category") or _FALLBACK_CATEGORY
        by_category[category] = by_category.get(category, 0) + 1
        by_grounding[cp.get("grounding") or DEFAULT_GROUNDING] = (
            by_grounding.get(cp.get("grounding") or DEFAULT_GROUNDING, 0) + 1
        )
        if cp.get("review_status"):
            flagged += 1
        capped += int(cp.get("capped_variants") or 0)
        if cp.get("coverage_gap"):
            gaps.append({
                "behaviour_key": cp.get("behaviour_key"),
                "category": category,
                "missing": cp["coverage_gap"],
            })

    total = len(checkpoints) or 1
    non_positive = by_type.get("negative", 0) + by_type.get("edge", 0)
    return {
        "total_checkpoints": len(checkpoints),
        "by_test_type": by_type,
        "by_category": by_category,
        "by_grounding": by_grounding,
        "ai_category_checkpoints": sum(
            count for code, count in by_category.items() if code in AI_CATEGORIES
        ),
        # The metric to watch. < 0.4 means the extractor has drifted back to
        # happy-path-only output.
        "negative_edge_ratio": round(non_positive / total, 3),
        "needs_review": flagged,
        # Lower-priority variants dropped by Stage 4c. A non-zero value is not
        # an error — it is the deliberate cap doing its job — but it belongs
        # in the scorecard so "this part produced 40 checkpoints" can be told
        # apart from "this part produced 55 and kept 40".
        "capped_variants": capped,
        "coverage_gaps": gaps,
        "excluded_zone_count": len(excluded_zones or []),
        "excluded_zone_kinds": sorted(
            {str(z.get("zone_kind")) for z in (excluded_zones or []) if z.get("zone_kind")}
        ),
    }


def ratio_gate_warning(coverage: dict | None) -> str | None:
    """Human-readable reason the coverage scorecard failed its quality gate,
    or None when it passed (or when there is not enough data to judge).

    Split out of the worker so the condition is unit-testable without a
    database, a Celery task or a provider — the whole point of the gate is
    that it fires reliably, and a check that can only be exercised by running
    a real ingest is a check nobody verifies.

    Deliberately silent in two cases, both of which would otherwise produce a
    warning that is technically true and operationally useless:

      * fewer than _RATIO_GATE_MIN_CHECKPOINTS — the ratio is noise at that
        size (see the constant).
      * zero checkpoints — the part was entirely non-testable, which is a
        CORRECT outcome for a pricing or timeline section, not a regression.
    """
    if not coverage:
        return None
    total = coverage.get("total_checkpoints") or 0
    if total < _RATIO_GATE_MIN_CHECKPOINTS:
        return None

    ratio = coverage.get("negative_edge_ratio")
    if ratio is None or ratio >= NEGATIVE_EDGE_RATIO_GATE:
        return None

    by_type = coverage.get("by_test_type") or {}
    return (
        f"negative+edge coverage is {ratio:.0%} of {total} checkpoint(s) "
        f"(negative={by_type.get('negative', 0)}, edge={by_type.get('edge', 0)}), "
        f"below the {NEGATIVE_EDGE_RATIO_GATE:.0%} acceptance gate — extraction "
        f"has drifted towards happy-path-only output. Checkpoints are kept; "
        f"investigate the extraction prompt or the model provider, not the document"
    )


# ── Stage 6: cross-part reconciliation ───────────────────────────────────────
#
# Extraction runs per part, and dedupe() (Stage 5) runs inside it — so it can
# only ever see one part at a time. A SOW almost always describes a feature
# twice: once in a summary or scope section near the front, once in detail
# later. Those land in different parts, get different behaviour_keys because
# the model named them differently, and produce two near-identical sets of
# checkpoints and therefore two near-identical sets of Skills. That is the
# duplicate-skills complaint, and it is invisible to every per-part stage.
#
# This stage runs once at the DOCUMENT level, over every analysed part, in two
# steps with deliberately different mechanisms:
#
#   1. MERGING is decided in CODE, by string similarity on the objective
#      within a test type, across parts only. No model involved. This is the
#      step that must not be wrong, so it does not get to be creative — and
#      because it is deterministic it can run on every part completion, which
#      is what stops a duplicate Skill being created in the first place.
#      Note it deliberately does NOT require the behaviour_keys to match: two
#      parts naming the same behaviour differently is the common case, and
#      that is exactly what has to merge.
#   2. NAMING is a judgement call, which is what a model is for. Once the
#      document is complete, an LLM maps near-duplicate behaviour_keys onto
#      one canonical key ("create-a-job" / "job-creation") so a behaviour's
#      variants group together in the Skills tab. Restricted to keys actually
#      sent and fails open to identity, exactly like
#      sow_drafting._canonicalize_headings. Once per document, not per part.
#
# THE ASYMMETRY THAT SETS THE THRESHOLD. Failing to merge two duplicates costs
# a cosmetic duplicate skill someone deletes. Wrongly merging two DIFFERENT
# tests silently deletes a test — and nobody finds out, because the thing that
# would have told them is the test that no longer exists. Those costs are not
# comparable, so the bar is set high and the guards below are conservative.

_RECONCILE_SYSTEM = (
    "You are consolidating behaviour names extracted from a single "
    "requirements document. It was processed in sections, so the same product "
    "behaviour may have been named differently in each (e.g. "
    "\"create-a-job\" and \"job-creation\"). Map each name onto a canonical "
    "name. Respond with JSON only:\n"
    '{"mapping": [{"from": str, "to": str}]}\n'
    "\n"
    "Rules:\n"
    "- Every input name must appear exactly once as a 'from'.\n"
    "- Names for the SAME behaviour share a 'to'; use the clearest of them "
    "as the canonical form.\n"
    "- A name with no near-duplicate maps to ITSELF.\n"
    "- 'to' must always be one of the input names. Do not invent names.\n"
    "- Do NOT merge behaviours that merely belong to the same feature area. "
    "\"create-a-job\" and \"edit-a-job\" are different behaviours and must "
    "keep different names. Merging them loses tests."
)

# Objective similarity above which two checkpoints of the same canonical
# behaviour and the same test type are treated as the same test. Set high on
# purpose: see the asymmetry note above. 0.90 merges "Create a job from the
# jobs list" / "Create a job from the jobs list page" and leaves "Reject an
# empty title" / "Reject a duplicate title" — two genuinely different negative
# cases — alone.
_RECONCILE_OBJECTIVE_THRESHOLD = 0.90

# Above this many distinct behaviours the naming call is skipped and only
# exact-key matching is used. The prompt would otherwise grow with the
# document, and a mapping over hundreds of names is where a model starts
# merging things that are merely adjacent.
_MAX_RECONCILE_KEYS = 120


class ReconcileResult:
    """What document-level reconciliation decided.

    absorbed maps part_number -> the set of that part's checkpoint indices
    that were merged away, so the caller can skip creating Skills for them
    WITHOUT having to rewrite the part's own stored checkpoints. Keeping each
    SowPart.checkpoints as the untouched record of what that section actually
    produced is the same rule Stage 0 follows for text: a stage may select,
    it may not rewrite the evidence.
    """

    __slots__ = ("checkpoints", "absorbed", "merged_count", "model_used")

    def __init__(self, checkpoints, absorbed, merged_count, model_used):
        self.checkpoints = checkpoints
        self.absorbed = absorbed
        self.merged_count = merged_count
        self.model_used = model_used


def _objective_text(cp: dict) -> str:
    return " ".join(str(cp.get("objective") or cp.get("description") or "").lower().split())


def canonicalize_behaviour_keys(keys_with_labels: dict[str, str]) -> tuple[dict[str, str], str]:
    """Map near-duplicate behaviour keys onto one canonical key.

    keys_with_labels: behaviour_key -> a human label (the behaviour's happy
    path objective) giving the model something to judge on; a slug alone is
    thin evidence for "are these the same thing".

    Returns (mapping, model_used). Falls back to identity — exact-key merging
    only — on every failure path. Nothing is at risk in that case: two
    near-duplicate behaviours survive as two, which is the pre-existing
    behaviour, not a loss.
    """
    unique = sorted(k for k in keys_with_labels if k)
    identity = {k: k for k in unique}
    if len(unique) < 2 or not reconcile_enabled():
        return identity, ""
    if len(unique) > _MAX_RECONCILE_KEYS:
        logger.warning(
            "TDD reconcile: %d distinct behaviours exceeds the %d-key limit for "
            "name consolidation — exact-key matching only, near-duplicates across "
            "parts will survive as separate behaviours",
            len(unique), _MAX_RECONCILE_KEYS,
        )
        return identity, ""

    listing = "\n".join(f"{k} :: {keys_with_labels.get(k, '')[:200]}" for k in unique)
    try:
        from app.services import llm_router

        result = llm_router.complete_json_complete(
            "Consolidate these behaviour names:\n\n" + listing,
            system=_RECONCILE_SYSTEM,
            max_tokens=4096,
        )
    except Exception:  # noqa: BLE001 — reconciliation is a tidying pass, never fatal
        logger.warning(
            "TDD reconcile: behaviour-name consolidation failed — keeping every "
            "behaviour as extracted", exc_info=True,
        )
        return identity, ""

    raw = result.parsed_json or {}
    entries = raw.get("mapping", []) if isinstance(raw, dict) else []
    mapping = dict(identity)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        src = str(entry.get("from") or "").strip()
        dst = str(entry.get("to") or "").strip()
        # Both ends must be keys we actually sent. A canonical form the model
        # invented wholesale is not allowed to replace a real one, and a
        # 'from' we never sent has nothing to remap.
        if src in mapping and dst in identity:
            mapping[src] = dst
    return mapping, result.model_used


def reconcile_across_parts(parts: list[dict], *, finalize: bool = False) -> ReconcileResult:
    """Stage 6. Merge duplicate checkpoints across a document's parts.

    parts: [{"part_number": int, "checkpoints": [dict, ...]}, ...] in part
    order. Returns the document-level checkpoint list plus which of each
    part's checkpoints were absorbed into an earlier part's.

    Merging itself is deterministic and free, so this runs on every part
    completion — that is what prevents a duplicate Skill being created, as
    opposed to cleaning one up afterwards.

    finalize=True additionally runs the one LLM naming-consolidation call
    (§ step 2 above). The caller passes it when the document's last part has
    just finished, so a 12-part document pays for one call rather than twelve
    — and an incomplete document is not consolidated against names that are
    still arriving.

    Never raises: a document whose parts all analysed successfully must not be
    failed by a tidying pass. On any failure the plain concatenation — the
    previous behaviour — is returned.
    """
    ordered = sorted(parts, key=lambda p: p.get("part_number") or 0)
    flat: list[tuple[int, int, dict]] = [
        (p.get("part_number") or 0, i, cp)
        for p in ordered
        for i, cp in enumerate(p.get("checkpoints") or [])
        if isinstance(cp, dict)
    ]
    concatenated = [cp for _, _, cp in flat]
    if not reconcile_enabled() or len({pn for pn, _, _ in flat}) < 2:
        # One part cannot have cross-part duplicates, and Stage 5 already
        # deduped within it.
        return ReconcileResult(concatenated, {}, 0, "")

    try:
        merged: list[dict] = []
        absorbed: dict[int, set[int]] = {}
        # test_type -> [(survivor dict, objective)]. Bucketed by test type
        # only: requiring matching behaviour_keys would miss exactly the case
        # this stage exists for, where two parts named the same behaviour
        # differently. A positive can never absorb a negative.
        survivors: dict[str, list[tuple[dict, str]]] = {}

        for part_number, index, cp in flat:
            bucket_id = str(cp.get("test_type") or "")
            objective = _objective_text(cp)
            bucket = survivors.setdefault(bucket_id, [])

            duplicate_of = None
            for survivor, survivor_objective in bucket:
                # Only ACROSS parts. Two checkpoints left in one part after
                # Stage 5 are deliberately distinct — a rich input_validation
                # behaviour legitimately has several negative cases, and
                # collapsing those here would delete real tests.
                if survivor.get("_part_number") == part_number:
                    continue
                if not objective or not survivor_objective:
                    continue
                ratio = difflib.SequenceMatcher(None, objective, survivor_objective).ratio()
                if ratio >= _RECONCILE_OBJECTIVE_THRESHOLD:
                    duplicate_of = survivor
                    break

            if duplicate_of is not None:
                # First occurrence wins, so document order stays stable and
                # re-analysing a later part cannot reshuffle the list.
                absorbed.setdefault(part_number, set()).add(index)
                sources = duplicate_of.setdefault("merged_from_parts", [])
                if part_number not in sources:
                    sources.append(part_number)
                continue

            copy = dict(cp)
            # Private to this function: stripped below so it never reaches
            # storage or the API.
            copy["_part_number"] = part_number
            merged.append(copy)
            bucket.append((copy, objective))

        merged_count = sum(len(v) for v in absorbed.values())

        # Naming consolidation last, and only on the completed document: it
        # affects how variants GROUP, never which of them survive, so it can
        # be skipped entirely without changing what the document contains.
        model_used = ""
        if finalize:
            labels: dict[str, str] = {}
            for cp in merged:
                key = str(cp.get("behaviour_key") or "")
                if key and (key not in labels or cp.get("test_type") == "positive"):
                    labels[key] = _objective_text(cp) or str(cp.get("title") or "")
            mapping, model_used = canonicalize_behaviour_keys(labels)
            for cp in merged:
                original = str(cp.get("behaviour_key") or "")
                cp["behaviour_key"] = mapping.get(original, original)

        for cp in merged:
            cp.pop("_part_number", None)

        if merged_count:
            logger.info(
                "TDD reconcile: %d duplicate checkpoint(s) merged across %d part(s) "
                "— %d checkpoint(s) in the document list%s",
                merged_count, len(ordered), len(merged),
                f" (naming consolidated via {model_used})" if model_used else "",
            )
        return ReconcileResult(merged, absorbed, merged_count, model_used)
    except Exception:  # noqa: BLE001 — never fail a document over a tidying pass
        logger.warning(
            "TDD reconcile: cross-part reconciliation failed — falling back to "
            "plain concatenation of every part's checkpoints", exc_info=True,
        )
        return ReconcileResult(concatenated, {}, 0, "")


# ── Orchestrator ─────────────────────────────────────────────────────────────

class ExtractionResult:
    """What one part's extraction produced.

    `checkpoints` is deliberately the same shape the legacy pipeline
    returned, plus the new fields — so SowPart.checkpoints, the merge into
    DesignRule, and every existing consumer keep working untouched.
    """

    __slots__ = ("checkpoints", "excluded_zones", "coverage", "model_used")

    def __init__(
        self,
        checkpoints: list[dict],
        excluded_zones: list[dict],
        coverage: dict,
        model_used: str,
    ) -> None:
        self.checkpoints = checkpoints
        self.excluded_zones = excluded_zones
        self.coverage = coverage
        self.model_used = model_used


def extract(
    text: str,
    *,
    part_label: str | None = None,
    ui_inventory: str | None = None,
    on_progress=None,
) -> ExtractionResult:
    """Full Stage 0-5 pipeline over one part's text.

    Raises IngestError only when extraction itself fails on every provider —
    identical to design_ingest.parse_sow, so the worker's error path is
    unchanged.
    """
    # Every report() below describes work that has ALREADY happened, not work
    # about to start, and a stage that does not run emits nothing at all.
    # That is the difference between this and a fixed list of phases: the
    # panel shows zoning only when zoning ran, repair only when there was a
    # gap to repair, and says so when a stage was skipped.
    from app.services.sow_progress import DONE, SKIPPED, report

    segments = split_segments(text)
    report(
        on_progress, "segment", DONE,
        f"Split the text into {len(segments)} section"
        f"{'' if len(segments) == 1 else 's'}",
        {"segments": len(segments)},
    )

    # Zoning reports itself (it is the only code that can tell an exclusion
    # from a discarded verdict — see classify_zones).
    testable, excluded, zoning_model = classify_zones(segments, on_progress=on_progress)

    if not testable:
        logger.info(
            "TDD extraction: every segment of this part is non-testable "
            "(%s) — no LLM extraction call made",
            ", ".join(sorted({str(z.get("zone_kind")) for z in excluded})) or "unclassified",
        )
        report(
            on_progress, "extract", SKIPPED,
            "Nothing testable here — no requirements to extract from this part",
        )
        return ExtractionResult([], excluded, scorecard([], excluded), zoning_model)

    testable_text = "\n\n".join(seg["body"] for seg in testable)
    checkpoints, extraction_model = extract_behaviours(
        testable_text, part_label=part_label, ui_inventory=ui_inventory
    )
    behaviours = len({cp.get("behaviour_key") for cp in checkpoints})
    report(
        on_progress, "extract", DONE,
        f"Read {behaviours} behaviour{'' if behaviours == 1 else 's'} out of the "
        f"requirements and wrote {len(checkpoints)} test"
        f"{'' if len(checkpoints) == 1 else 's'}",
        {"behaviours": behaviours, "checkpoints": len(checkpoints)},
    )

    gaps_before = sum(len(cp.get("coverage_gap") or []) for cp in checkpoints)
    # Stage 4b before dedupe, so a repaired variant that happens to restate an
    # existing one is collapsed by the same rule as everything else rather
    # than surviving as a near-duplicate skill.
    checkpoints, repair_model = repair_coverage_gaps(
        checkpoints, part_label=part_label, ui_inventory=ui_inventory
    )
    if not gaps_before:
        report(
            on_progress, "repair", SKIPPED,
            "Every behaviour already had the negative and edge cases its "
            "category requires — nothing to fill in",
        )
    else:
        gaps_after = sum(len(cp.get("coverage_gap") or []) for cp in checkpoints)
        filled = max(gaps_before - gaps_after, 0)
        report(
            on_progress, "repair", DONE if filled else SKIPPED,
            f"Filled {filled} of {gaps_before} missing negative/edge case"
            f"{'' if gaps_before == 1 else 's'}"
            + (f"; {gaps_after} still flagged as a coverage gap" if gaps_after else ""),
            {"gaps_before": gaps_before, "filled": filled, "gaps_after": gaps_after},
        )

    before_dedupe = len(checkpoints)
    checkpoints = dedupe(checkpoints)
    if before_dedupe != len(checkpoints):
        report(
            on_progress, "dedupe", DONE,
            f"Collapsed {before_dedupe - len(checkpoints)} repeated test"
            f"{'' if before_dedupe - len(checkpoints) == 1 else 's'}",
            {"removed": before_dedupe - len(checkpoints)},
        )

    models = [m for m in (extraction_model, repair_model, zoning_model) if m]
    return ExtractionResult(
        checkpoints,
        excluded,
        scorecard(checkpoints, excluded),
        ", ".join(dict.fromkeys(models)),
    )
