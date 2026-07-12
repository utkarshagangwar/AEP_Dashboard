"""Shared AISkill upsert logic.

Two independent paths write to the same ai_skills table, so both funnel
through here to guarantee identical hashing/identity rules:

  * A passed goal-based AI test run auto-saves its recorded browser-use
    action history (see app.workers.tasks.ai_execution._maybe_save_skill),
    keyed by goal_hash (a hash of the exact goal text).
  * SOW/video checkpoint parsing (app.workers.tasks.sow_ingest /
    video_ingest) saves a detailed prompt instruction for each functional
    checkpoint as soon as parsing finishes — no live browser run required.
    These "prompt skills" have history_json=None until someone actually
    runs one (via the Skills tab) and it passes, at which point the normal
    goal-based auto-save above finds this same row by goal_hash (the run's
    goal is the skill's instruction text verbatim) and fills in a real
    recording — no special-casing needed for that upgrade.
"""
from __future__ import annotations

import hashlib
import re


def compute_goal_hash(goal: str) -> str:
    """Normalize + hash a goal/instruction string. Whitespace-insensitive so
    trivial formatting differences don't produce a different identity."""
    normalized = " ".join(goal.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:200]


def upsert_prompt_skill(
    db,
    *,
    title: str,
    instruction: str,
    source_type: str,
    artifact_id,
    project_id=None,
    created_by=None,
):
    """Create or refresh a prompt-only skill extracted from a parsed SOW/video
    checkpoint (no recorded browser actions — just a detailed instruction).

    Identity is (artifact_id, normalized title): re-analyzing the same part
    (the Analyse/Retry buttons) updates the existing row's instruction text
    in place instead of creating a duplicate, since the checkpoint's title
    is expected to stay stable across re-parses even as the instruction text
    is refined. Does not commit — caller controls the transaction.

    If a human has since edited this skill by hand (manually_edited=True),
    its name/goal/project are left untouched — re-parsing must never
    silently clobber a deliberate manual edit. Provenance (source_type/
    source_artifact_id) is still refreshed either way.
    """
    from app.models.ai_runs import AISkill

    source_key = f"{artifact_id}:{_slug(title)}"[:300]
    goal_hash = compute_goal_hash(instruction)

    skill = db.query(AISkill).filter(AISkill.source_key == source_key).one_or_none()
    if skill is None:
        # Fall back to exact-content match in case this precise instruction
        # was already saved under a different key (e.g. a goal-based run).
        skill = db.query(AISkill).filter(AISkill.goal_hash == goal_hash).one_or_none()

    name = title.strip()[:300] or (instruction.strip()[:117] + "...")

    if skill is not None:
        skill.source_type = source_type
        skill.source_artifact_id = artifact_id
        if not skill.manually_edited:
            skill.name = name
            skill.goal = instruction
            skill.goal_hash = goal_hash
            skill.source_key = source_key
            if project_id is not None:
                skill.project_id = project_id
    else:
        skill = AISkill(
            name=name,
            goal=instruction,
            goal_hash=goal_hash,
            source_key=source_key,
            source_type=source_type,
            source_artifact_id=artifact_id,
            project_id=project_id,
            history_json=None,
            step_count=0,
            created_by=created_by,
        )
        db.add(skill)
    return skill
