"""Flow anchoring for extracted TDDs.

WHAT THIS IS FOR. A checkpoint can be a perfectly faithful reading of the
source document and still be impossible to run. "Verify skills regenerate when
the Job Description changes" is exactly what the requirements say, but a tester
handed that sentence has no job, no workspace and no session — there is nowhere
to start. The extractor cannot see this, because the section it read does not
mention the six screens that come before it. The document is organised by
subject; execution is organised by state.

So this module holds a checkpoint to one extra question: **from a cold start,
can a tester reach the point where this test begins?** A checkpoint that cannot
name a reachable starting state is not wrong, it is not runnable, and the two
failures need different handling by a human.

NOTHING IS EVER DROPPED. An unreachable checkpoint is annotated with
review_status="needs_design_flow" and a reason, exactly like the testability
gate records what it excluded rather than deleting it. That review status
already existed for this case; until now there was nothing to check against.

FAILS OPEN, ALWAYS. Flow validation is an advisory pass over work that has
already succeeded. A malformed flow model, a cycle, a provider error — none of
them may cost an extraction. Every path returns the checkpoints it was given.

WHEN THERE IS NO FLOW MODEL — the common case today — validate() returns the
checkpoints untouched and reports that it did nothing. Every project without a
flow model behaves exactly as it did before this module existed.

THE FLOW MODEL. A per-project object describing the platform's execution
states, supplied by get_flow_model(). Minimum viable shape:

    {"states": [
       {"id": "S00_ANON", "requires": []},
       {"id": "S01_AUTHENTICATED", "requires": ["S00_ANON"],
        "locked_behaviours": [...], "pages": ["login"]}
    ]}

`requires` builds the graph; everything else is optional. `entry_state`
defaults to the single state with no requirements.
"""
from __future__ import annotations

import json
import os
from collections import deque

from app.core.logging import get_logger

logger = get_logger(__name__)


# ── Configuration ────────────────────────────────────────────────────────────

def _flag(name: str, default: bool = True) -> bool:
    """Read a boolean env flag. Opt-out convention: unset means enabled.

    Deliberately duplicated from tdd_extraction rather than imported, so this
    module has no import-time dependency on the extractor it annotates.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return raw not in ("0", "false", "False", "no", "off")


def flow_validation_enabled() -> bool:
    return _flag("TDD_FLOW_VALIDATION")


# Failure codes. Stored verbatim in review_reason so a reviewer sees the same
# vocabulary the tests and the docs use, and so a frequency count over reasons
# tells you which prompt rule to tighten next.
E_NO_STATE = "flow:no_precondition_state"
E_UNKNOWN_STATE = "flow:unknown_state"
E_UNREACHABLE = "flow:unreachable_state"
E_LOCKED = "flow:asserts_locked_behaviour"

_REASON_TEXT = {
    E_NO_STATE: "names no precondition state, so it has no defined starting point",
    E_UNKNOWN_STATE: "names a precondition state that is not in the project's flow model",
    E_UNREACHABLE: "names a state that cannot be reached from the entry state",
    E_LOCKED: "asserts a behaviour that is permanently locked by the time this state is reached",
}

# Phrases that mark an expected result as already asserting a block. A
# checkpoint whose expected result is "the system prevents it" is the CORRECT
# way to test a locked behaviour, so it must not be flagged for mentioning one.
_NEGATED = (
    "blocked", "not allowed", "cannot", "can not", "is disabled", "rejected",
    "prevented", "error is shown", "error message", "not supported",
    "not permitted", "refuses", "denied", "must not",
)


# ── Flow model provider ──────────────────────────────────────────────────────

def get_flow_model(session=None, project_id=None) -> dict | None:
    """Return the flow model for a project, or None if it has none.

    THIS WAS THE SEAM. It now prefers Project Intelligence's verified
    pi_flows row (app.services.pi_flow.get_verified_model) for `project_id`,
    when Project Intelligence is enabled and has one; otherwise — PI is off,
    has no verified model yet for this project, or the lookup itself
    failed — it falls through to the original TDD_FLOW_MODEL_PATH escape
    hatch unchanged, so every project that predates Project Intelligence, or
    every project PI simply hasn't learned yet, behaves exactly as it did
    before this function's body changed.

    `session`/`project_id` were always this function's real signature (see
    ui_inventory.get_inventory_text(session, project_id), the sibling this
    mirrors) even though the file-based fallback never used them — that's
    what made this a seam in the first place, not a new integration.

    Never raises: a project whose flow model is missing, malformed, or
    whose lookup errored is a project without flow validation, not a failed
    ingest — identical contract to before.
    """
    if session is not None and project_id is not None:
        try:
            from app.services import pi_flow

            model = pi_flow.get_verified_model(session, project_id)
            if model:
                return model
        except Exception:  # noqa: BLE001 — fall through to the file-based path
            logger.warning(
                "Flow validation: Project Intelligence flow lookup failed for "
                "project %s — falling back to TDD_FLOW_MODEL_PATH", project_id,
                exc_info=True,
            )

    path = os.environ.get("TDD_FLOW_MODEL_PATH", "").strip()
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            model = json.load(fh)
    except FileNotFoundError:
        logger.warning("Flow validation: TDD_FLOW_MODEL_PATH=%s does not exist", path)
        return None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("Flow validation: could not read flow model at %s: %s", path, exc)
        return None
    if not isinstance(model, dict) or not isinstance(model.get("states"), list):
        logger.warning(
            "Flow validation: flow model at %s has no 'states' list — ignoring", path
        )
        return None
    return model


# ── Graph ────────────────────────────────────────────────────────────────────

def build_index(flow_model: dict) -> tuple[dict[str, dict], str | None]:
    """Return (states_by_id, entry_state_id).

    A state whose `requires` names an unknown state keeps the reference; the
    dangling edge simply never resolves, which surfaces as unreachable rather
    than as an exception. Returning ({}, None) means the model is unusable and
    validation is skipped — the fail-open path.
    """
    states_by_id: dict[str, dict] = {}
    for state in flow_model.get("states") or []:
        if not isinstance(state, dict):
            continue
        sid = state.get("id")
        if isinstance(sid, str) and sid and sid not in states_by_id:
            states_by_id[sid] = state
    if not states_by_id:
        return {}, None

    entry = flow_model.get("entry_state")
    if entry not in states_by_id:
        roots = [s for s, v in states_by_id.items() if not (v.get("requires") or [])]
        # Exactly one root is the well-formed case. Several means the author
        # has not said where a run starts, and picking one arbitrarily would
        # silently change which checkpoints validate.
        entry = roots[0] if len(roots) == 1 else None
    return states_by_id, entry


def reachable_states(states_by_id: dict[str, dict], entry: str) -> set[str]:
    """Forward BFS from the entry state over the `requires` edges."""
    forward: dict[str, list[str]] = {}
    for sid, state in states_by_id.items():
        for req in state.get("requires") or []:
            if req in states_by_id:
                forward.setdefault(req, []).append(sid)

    seen = {entry}
    queue = deque([entry])
    while queue:
        for nxt in forward.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def resolve_setup_path(target: str, states_by_id: dict[str, dict]) -> list[str]:
    """The ordered chain of states from the entry point to `target`.

    Depth-first over `requires`, emitting each state after its prerequisites.
    A cycle in the model returns [] rather than recursing forever — the model
    is broken, and a broken model must not take the extraction down with it.
    """
    order: list[str] = []
    visiting: set[str] = set()

    def visit(sid: str) -> bool:
        if sid in order:
            return True
        if sid in visiting or sid not in states_by_id:
            return False
        visiting.add(sid)
        for req in states_by_id[sid].get("requires") or []:
            if not visit(req):
                return False
        visiting.discard(sid)
        order.append(sid)
        return True

    return order if visit(target) else []


def collect_locks(path: list[str], states_by_id: dict[str, dict]) -> set[str]:
    """Every behaviour locked anywhere on the path is locked at its end.

    Locks are cumulative and are never released — that is what makes them
    checkable at all. A behaviour locked at state 6 is still locked at state
    11, so a checkpoint anchored late inherits every earlier lock.
    """
    locked: set[str] = set()
    for sid in path:
        for behaviour in states_by_id.get(sid, {}).get("locked_behaviours") or []:
            if isinstance(behaviour, str) and behaviour.strip():
                locked.add(behaviour.strip().lower())
    return locked


# ── Validation ───────────────────────────────────────────────────────────────

def _page_state_hint(cp: dict, states_by_id: dict[str, dict]) -> str | None:
    """Resolve a checkpoint's state from its `page` via an author-supplied map.

    Only consults `states[].pages`, which a human wrote. This is a lookup, not
    an inference: if the flow model does not map pages, nothing is guessed.
    """
    page = (cp.get("page") or "").strip().lower()
    if not page:
        return None
    for sid, state in states_by_id.items():
        for candidate in state.get("pages") or []:
            if isinstance(candidate, str) and candidate.strip().lower() == page:
                return sid
    return None


def _asserts_locked(cp: dict, locked: set[str]) -> str | None:
    """The locked behaviour this checkpoint claims is permitted, if any.

    A checkpoint testing that the system BLOCKS a locked behaviour is correct
    and is not flagged — only one claiming it succeeds.
    """
    if not locked:
        return None
    haystack = " ".join(
        str(part) for part in (
            cp.get("title"), cp.get("objective"), cp.get("expected"),
            " ".join(str(i) for i in (cp.get("instructions") or [])),
        ) if part
    ).lower()
    if not haystack:
        return None
    if any(phrase in haystack for phrase in _NEGATED):
        return None
    for behaviour in locked:
        if behaviour and behaviour in haystack:
            return behaviour
    return None


def validate(checkpoints: list[dict], flow_model: dict | None) -> dict:
    """Anchor each checkpoint to the flow, in place. Returns a summary.

    Adds, only when a flow model was actually applied:
      precondition_state  the state the checkpoint starts from
      setup_path          ordered states from the entry point to it

    Flags, never drops: an unrunnable checkpoint keeps its content and gains
    review_status="needs_design_flow" plus a review_reason naming the code.
    An existing review_status is never overwritten — a checkpoint the
    extractor already flagged keeps the reason it was flagged for.

    Returns {} when validation did not run, which is what the caller uses to
    decide whether to report the stage at all.
    """
    if not checkpoints or not flow_model or not flow_validation_enabled():
        return {}

    try:
        states_by_id, entry = build_index(flow_model)
        if not states_by_id or not entry:
            logger.warning(
                "Flow validation: flow model has no usable states or no single "
                "entry state — skipping"
            )
            return {}

        reachable = reachable_states(states_by_id, entry)
        # Cache per state: the path and its cumulative locks are identical for
        # every checkpoint anchored to the same state, and a document part can
        # carry hundreds.
        path_cache: dict[str, list[str]] = {}
        lock_cache: dict[str, set[str]] = {}
        by_state: dict[str, int] = {}
        by_reason: dict[str, int] = {}
        anchored = 0

        for cp in checkpoints:
            state = cp.get("precondition_state") or _page_state_hint(cp, states_by_id)
            reason: str | None = None

            if not state:
                reason = E_NO_STATE
            elif state not in states_by_id:
                reason = E_UNKNOWN_STATE
            elif state not in reachable:
                reason = E_UNREACHABLE
            else:
                if state not in path_cache:
                    path_cache[state] = resolve_setup_path(state, states_by_id)
                    lock_cache[state] = collect_locks(path_cache[state], states_by_id)
                path = path_cache[state]
                if not path:
                    # Only reachable-but-unresolvable cause is a cycle.
                    reason = E_UNREACHABLE
                else:
                    breached = _asserts_locked(cp, lock_cache[state])
                    if breached:
                        reason = E_LOCKED
                        cp["flow_locked_behaviour"] = breached
                    cp["precondition_state"] = state
                    cp["setup_path"] = list(path)
                    anchored += 1
                    by_state[state] = by_state.get(state, 0) + 1

            if reason:
                by_reason[reason] = by_reason.get(reason, 0) + 1
                # Never overwrite an existing flag: "the document did not
                # specify this well enough to execute" outranks "and it also
                # has no reachable starting point".
                if not cp.get("review_status"):
                    cp["review_status"] = "needs_design_flow"
                    cp["review_reason"] = (
                        f"{reason} — this checkpoint {_REASON_TEXT[reason]}."
                    )

        return {
            "states_in_model": len(states_by_id),
            "entry_state": entry,
            "anchored": anchored,
            "unanchored": len(checkpoints) - anchored,
            "by_state": by_state,
            "by_reason": by_reason,
        }
    except Exception:  # noqa: BLE001 — advisory pass, must never fail an ingest
        logger.exception("Flow validation failed — checkpoints returned unanchored")
        return {}


# ── Prompt fragment ──────────────────────────────────────────────────────────

_MAX_PROMPT_STATES = 40
_MAX_LOCKS_PER_STATE = 8


def render_flow_reference(flow_model: dict | None) -> str:
    """The flow rules appended to the extraction system prompt.

    Returns "" when there is no usable flow model, which is what keeps the
    prompt byte-identical for every project that has none.
    """
    if not flow_model or not flow_validation_enabled():
        return ""
    try:
        states_by_id, entry = build_index(flow_model)
        if not states_by_id or not entry:
            return ""

        lines = [
            "",
            "PLATFORM EXECUTION FLOW — BINDING.",
            "A test is only useful if a tester can reach the point where it starts.",
            "Every checkpoint MUST carry a \"precondition_state\" field naming the ONE "
            "state below at which its first instruction becomes possible.",
            f"Runs begin at {entry}.",
            "If no state below fits, still emit the checkpoint and set "
            "\"precondition_state\": null rather than guessing.",
            "",
            "States (id — what must already be true):",
        ]
        for sid in list(states_by_id)[:_MAX_PROMPT_STATES]:
            state = states_by_id[sid]
            requires = ", ".join(state.get("requires") or []) or "nothing"
            note = str(state.get("name") or state.get("description") or "").strip()
            lines.append(f"  {sid} — after {requires}" + (f". {note}" if note else ""))
            locks = [
                str(b) for b in (state.get("locked_behaviours") or [])
                if isinstance(b, str) and b.strip()
            ][:_MAX_LOCKS_PER_STATE]
            if locks:
                lines.append(
                    f"      permanently unavailable from here on: {', '.join(locks)}"
                )
        if len(states_by_id) > _MAX_PROMPT_STATES:
            lines.append(f"  ... and {len(states_by_id) - _MAX_PROMPT_STATES} more")

        lines += [
            "",
            "Locked behaviours are cumulative and are never released. A checkpoint "
            "whose state is at or past a lock must NOT assert that the behaviour "
            "succeeds — the only correct expectation is that the system blocks it.",
        ]
        return "\n".join(lines) + "\n"
    except Exception:  # noqa: BLE001 — a bad model costs the rules, not the run
        logger.exception("Flow validation: could not render flow reference")
        return ""
