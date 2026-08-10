"""Project UI label inventory — the vocabulary pass.

WHAT IT IS. One vision call per project that reads the uploaded evidence
(screenshots, plus labels already recovered from digested walkthrough videos)
and writes down what the platform's screens, buttons and fields are actually
CALLED. The result is stored on the project and handed to the SOW extraction
prompt as text.

WHY IT EXISTS. Extraction only ever saw the requirements document, so a
checkpoint said "click Submit Application" because that is what the document
called it, while the product says "Apply Now". The test then fails for a
reason that is neither a product defect nor a spec gap — the most
demoralising red result there is, because it looks like a bug and isn't.

WHY NOT LIVE NAVIGATION. Having an agent drive the real product per test
would ground the same labels, at per-test cost, and would need working
credentials and a deployed environment at extraction time. Today a SOW for a
product that does not exist yet still extracts fine, and that property is
worth keeping. This pass costs ONE call per project, reused by every SOW
imported for it.

THE HARD RULE: VOCABULARY, NOT REQUIREMENTS. The inventory tells the
extractor what things are named. It must never be treated as evidence that a
behaviour exists — a button visible in a screenshot is not a requirement, and
letting the inventory introduce behaviours would reintroduce the "everything
becomes a TDD" defect from the other direction. That rule is stated in the
prompt below, restated in the extraction prompt that consumes the output, and
is the reason this module never returns anything but labels.

CONFIG (opt-out, matching the TDD_* convention):
  TDD_UI_INVENTORY=0   never build or inject an inventory; extraction sees
                       text only, exactly as it did before this existed.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger

logger = get_logger(__name__)


def inventory_enabled() -> bool:
    raw = os.environ.get("TDD_UI_INVENTORY", "").strip()
    if not raw:
        return True
    return raw not in ("0", "false", "False", "no", "off")


# Cost ceilings. A base64 image is a large prompt payload, and this call is
# the only place in the pipeline that sends several at once.
#
# Newest-first rather than oldest-first: if a project has accumulated more
# screenshots than the cap, the recent ones describe the product as it is now,
# and a stale label is worse than a missing one (a missing label falls back to
# the document's wording, which the reader can see; a wrong one looks
# authoritative).
_MAX_IMAGES = 8
_MAX_IMAGE_BYTES = 4 * 1024 * 1024
# Walkthrough-derived label hints are already-paid-for text: the video digest
# wrote instructions containing real on-screen labels. Feeding a bounded slice
# of them into the same call gets video coverage without decoding a frame.
_MAX_VIDEO_HINT_CHARS = 4000
_MAX_SCREENS = 40
_MAX_ITEMS_PER_SCREEN = 30
_MAX_LABEL_CHARS = 80

_INVENTORY_SYSTEM = (
    "You are reading screenshots of a software product to build a NAMING "
    "REFERENCE for a QA team.\n"
    "\n"
    "Your ONLY job is to record what things are called. You are not "
    "describing features, not judging the design, and not inferring what the "
    "product does.\n"
    "\n"
    "For each distinct screen you can see, record:\n"
    "  screen    — the screen's own title as shown, or the clearest name for "
    "it\n"
    "  controls  — buttons, links, tabs, toggles: their EXACT visible text\n"
    "  fields    — form inputs, dropdowns, checkboxes: their EXACT visible "
    "label\n"
    "  nav       — sidebar/menu/breadcrumb items, exact text\n"
    "  messages  — any error, empty-state or confirmation text visible\n"
    "\n"
    "Respond with JSON only:\n"
    '{"screens": [{"screen": str, "controls": [str], "fields": [str], '
    '"nav": [str], "messages": [str]}]}\n'
    "\n"
    "Rules:\n"
    "  1. TRANSCRIBE, never paraphrase. If the button reads \"Apply Now\", "
    "write \"Apply Now\" — not \"Apply\", not \"Submit application\". The "
    "whole point is the exact string.\n"
    "  2. Record only text you can actually READ in an image. If a label is "
    "cut off, blurred or ambiguous, leave it out. A wrong label is worse than "
    "a missing one: a missing one falls back to the document's wording, a "
    "wrong one looks authoritative and sends a test to a control that does "
    "not exist.\n"
    "  3. Do not invent screens, states or controls that are merely implied "
    "— no \"there is probably a delete button\".\n"
    "  4. Omit values that are DATA rather than interface: a person's name, "
    "a job title in a list, a date, a count. Record the column heading, not "
    "the row.\n"
    "  5. Merge duplicates. The same nav bar on six screens is one nav list, "
    "not six.\n"
    "  6. If you cannot read any interface text at all, return "
    '{"screens": []}. That is a valid, correct answer.'
)


def _clean_labels(raw: object) -> list[str]:
    """Bounded, de-duplicated, order-preserving list of label strings."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        label = " ".join(str(item or "").split())[:_MAX_LABEL_CHARS]
        key = label.lower()
        if not label or key in seen:
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= _MAX_ITEMS_PER_SCREEN:
            break
    return out


def normalize_inventory(raw: object) -> list[dict]:
    """Validate the model's reply into the stored shape.

    Screens with a name but no labels are dropped: they carry no vocabulary,
    which is the only thing this pass exists to produce, and they would take
    up prompt budget that a real screen could use.
    """
    screens = raw.get("screens") if isinstance(raw, dict) else None
    if not isinstance(screens, list):
        return []

    out: list[dict] = []
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        screen = {
            "screen": " ".join(str(entry.get("screen") or "").split())[:_MAX_LABEL_CHARS]
            or "Unnamed screen",
            "controls": _clean_labels(entry.get("controls")),
            "fields": _clean_labels(entry.get("fields")),
            "nav": _clean_labels(entry.get("nav")),
            "messages": _clean_labels(entry.get("messages")),
        }
        if screen["controls"] or screen["fields"] or screen["nav"] or screen["messages"]:
            out.append(screen)
        if len(out) >= _MAX_SCREENS:
            break
    return out


def render_inventory(screens: list[dict]) -> str:
    """The exact text handed to the extraction prompt.

    Deliberately terse and label-shaped rather than prose: it is a glossary,
    and prose around it invites the model to read it as a description of what
    the product does.
    """
    if not screens:
        return ""
    lines: list[str] = []
    for screen in screens:
        lines.append(f"- {screen['screen']}")
        for key, heading in (
            ("nav", "nav"),
            ("controls", "buttons/links"),
            ("fields", "fields"),
            ("messages", "messages"),
        ):
            if screen.get(key):
                lines.append(f"    {heading}: {', '.join(screen[key])}")
    return "\n".join(lines)


def _evidence_artifacts(db, project_id):
    """Screenshot artifacts for this project, newest first."""
    from app.models.visual_qa import ArtifactType, DesignArtifact

    return (
        db.query(DesignArtifact)
        .filter(
            DesignArtifact.project_id == project_id,
            DesignArtifact.artifact_type == ArtifactType.figma_png,
        )
        .order_by(DesignArtifact.created_at.desc())
        .all()
    )


def _video_label_hints(db, project_id) -> tuple[str, list[str]]:
    """Labels already recovered from this project's digested walkthroughs.

    The video digest wrote instructions containing real on-screen control and
    field names — work already paid for. Reusing that text costs nothing and
    covers screens no screenshot captured, without decoding a single frame.

    Returns (hint text, artifact ids used) so the video artifacts count toward
    the staleness key too: uploading a new walkthrough must invalidate the
    inventory exactly as uploading a new screenshot does.
    """
    from app.models.visual_qa import ArtifactType, DesignArtifact, DesignRule

    rows = (
        db.query(DesignArtifact, DesignRule)
        .join(DesignRule, DesignRule.artifact_id == DesignArtifact.id)
        .filter(
            DesignArtifact.project_id == project_id,
            DesignArtifact.artifact_type == ArtifactType.video,
        )
        .order_by(DesignArtifact.created_at.desc())
        .all()
    )

    used: list[str] = []
    chunks: list[str] = []
    budget = _MAX_VIDEO_HINT_CHARS
    for artifact, rule in rows:
        used.append(str(artifact.id))
        for cp in (rule.checkpoints or []):
            if not isinstance(cp, dict):
                continue
            # Only OBSERVED checkpoints. A derived negative case was reasoned
            # from QA practice, not read off the screen, so its wording is not
            # evidence of what anything is called.
            if cp.get("grounding") == "derived":
                continue
            for step in (cp.get("instructions") or [])[:12]:
                text = " ".join(str(step).split())
                if not text:
                    continue
                if len(text) > budget:
                    break
                chunks.append(text)
                budget -= len(text)
        if budget <= 0:
            break
    return "\n".join(chunks), used


def _load_images(artifacts) -> tuple[list[str], list[str]]:
    """(base64 images, artifact ids used). Unreadable or oversized files are
    skipped and NAMED in the log — a silently dropped screenshot looks
    identical to one the model failed to read."""
    from app.services import llm_router

    images: list[str] = []
    used: list[str] = []
    for artifact in artifacts:
        if len(images) >= _MAX_IMAGES:
            logger.warning(
                "UI inventory: project has more than %d screenshots; using the "
                "%d newest and skipping the rest",
                _MAX_IMAGES, _MAX_IMAGES,
            )
            break
        path = artifact.storage_path or ""
        try:
            if not path or not os.path.exists(path):
                logger.warning(
                    "UI inventory: screenshot %s is missing from disk (%s) — skipped",
                    artifact.file_name, path or "no path",
                )
                continue
            if os.path.getsize(path) > _MAX_IMAGE_BYTES:
                logger.warning(
                    "UI inventory: screenshot %s is over the %dMB per-image cap — skipped",
                    artifact.file_name, _MAX_IMAGE_BYTES // (1024 * 1024),
                )
                continue
            images.append(llm_router.encode_image_file(path))
            used.append(str(artifact.id))
        except OSError:
            logger.warning(
                "UI inventory: could not read screenshot %s — skipped",
                artifact.file_name, exc_info=True,
            )
    return images, used


def _source_key(artifact_ids: list[str]) -> list[str]:
    """Order-independent staleness key."""
    return sorted(set(artifact_ids))


def build_inventory(db, project_id):
    """Build (or rebuild) the inventory for one project. Returns the row.

    Never raises. Every failure path writes build_error and returns a row with
    no rendered_text, because the caller's correct response to "no inventory"
    is always the same — extract from text alone, exactly as before — and a
    vision call must not be able to fail a SOW ingest.
    """
    from app.models.visual_qa import ProjectUiInventory

    row = (
        db.query(ProjectUiInventory)
        .filter(ProjectUiInventory.project_id == project_id)
        .one_or_none()
    )
    if row is None:
        row = ProjectUiInventory(project_id=project_id)
        db.add(row)

    screenshots = _evidence_artifacts(db, project_id)
    video_hints, video_ids = _video_label_hints(db, project_id)
    images, image_ids = _load_images(screenshots)

    # Stamp what evidence EXISTED, not what the build managed to use. Those
    # differ whenever a screenshot was skipped as oversized, capped by
    # _MAX_IMAGES, or belongs to a video that has not finished digesting —
    # and keying on "used" would make the stored set permanently unequal to
    # the current set, so every single part would rebuild and pay for another
    # vision call.
    row.source_artifact_ids = _source_key(_current_source_ids(db, project_id))

    if not images and not video_hints:
        row.inventory_json = []
        row.rendered_text = None
        row.screen_count = 0
        row.build_error = "no usable evidence uploaded for this project"
        logger.info(
            "UI inventory: project %s has no usable evidence — extraction will "
            "run on document text alone", project_id,
        )
        return row

    prompt = (
        "Build the naming reference for this product."
        if images
        else "Build the naming reference for this product from the observed steps below."
    )
    if video_hints:
        # Labelled as observed steps, not as an instruction to follow, so the
        # model treats them as a second source of real strings rather than as
        # a task.
        prompt += (
            "\n\nSteps observed in a walkthrough recording of this same product. "
            "The control and field names inside them were read off the screen and "
            "are real — use them, but do not invent screens around them:\n"
            + video_hints
        )

    try:
        from app.services import llm_router

        result = llm_router.complete_json_complete(
            prompt,
            system=_INVENTORY_SYSTEM,
            images_b64=images or None,
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001 — must never fail a SOW ingest
        row.build_error = f"vision call failed: {exc}"[:1000]
        logger.warning(
            "UI inventory: build failed for project %s — extraction will run on "
            "document text alone", project_id, exc_info=True,
        )
        return row

    screens = normalize_inventory(result.parsed_json or {})
    rendered = render_inventory(screens)
    row.inventory_json = screens
    row.rendered_text = rendered or None
    row.screen_count = len(screens)
    row.built_by_model = result.model_used
    row.build_error = None if rendered else "the model read no interface text in the evidence"

    logger.info(
        "UI inventory: project %s — %d screen(s), %d label(s) from %d screenshot(s)"
        "%s via %s",
        project_id,
        len(screens),
        sum(
            len(s["controls"]) + len(s["fields"]) + len(s["nav"]) + len(s["messages"])
            for s in screens
        ),
        len(images),
        f" and {len(video_ids)} walkthrough(s)" if video_ids else "",
        result.model_used,
    )
    return row


def get_inventory_text(db, project_id) -> str | None:
    """The prompt-ready inventory for a project, building it if needed.

    Rebuilds when the project's evidence set has changed since the last build
    — that is the answer to "we uploaded screenshots after importing the first
    SOW", and it is why source_artifact_ids is stored rather than just a
    timestamp.

    Returns None whenever there is nothing useful to inject, which every
    caller treats identically: extract from document text alone.
    """
    from app.models.visual_qa import ProjectUiInventory

    if project_id is None or not inventory_enabled():
        return None

    try:
        row = (
            db.query(ProjectUiInventory)
            .filter(ProjectUiInventory.project_id == project_id)
            .one_or_none()
        )
        current = _source_key(_current_source_ids(db, project_id))
        if row is None or _source_key(list(row.source_artifact_ids or [])) != current:
            row = build_inventory(db, project_id)
            db.flush()
        return row.rendered_text or None
    except Exception:  # noqa: BLE001 — context is an enhancement, never a gate
        logger.warning(
            "UI inventory: could not resolve inventory for project %s — "
            "extraction continues on document text alone", project_id, exc_info=True,
        )
        return None


def _current_source_ids(db, project_id) -> list[str]:
    """Every artifact that would feed a build right now.

    Deliberately computed from ALL of the project's evidence rather than from
    what the last build managed to use: a screenshot that was skipped as
    oversized still counts, so adding another one after it does not look like
    "nothing changed" and skip the rebuild.
    """
    from app.models.visual_qa import ArtifactType, DesignArtifact

    rows = (
        db.query(DesignArtifact.id)
        .filter(
            DesignArtifact.project_id == project_id,
            DesignArtifact.artifact_type.in_(
                [ArtifactType.figma_png, ArtifactType.video]
            ),
        )
        .all()
    )
    return [str(r[0]) for r in rows]


def format_for_prompt(rendered_text: str | None) -> str:
    """Wrap the inventory in the framing the extractor is held to.

    The framing is not decoration. Without the "vocabulary, not requirements"
    rule stated at the point of use, a list of buttons reads as a list of
    features, and the extractor starts writing checkpoints for controls
    nobody asked to be tested — the original defect, arriving from the
    opposite direction.
    """
    if not rendered_text:
        return ""
    return (
        "\n\n══ PRODUCT UI NAMING REFERENCE ══\n"
        "Screens, controls and fields as they are ACTUALLY LABELLED in this "
        "product, read from screenshots and walkthrough recordings:\n\n"
        f"{rendered_text}\n\n"
        "How to use it:\n"
        "  * When the document describes a control this reference also names, "
        "use the REFERENCE's exact wording in your instructions. The document "
        "may say \"Submit Application\" where the product's button reads "
        "\"Apply Now\"; a test written with the document's wording fails for a "
        "reason that is neither a product defect nor a spec gap.\n"
        "  * When a control is NOT in this reference, use the document's "
        "wording. Do not guess at a name, and do not assume the control is "
        "missing from the product — this reference is partial by nature.\n"
        "  * This reference is VOCABULARY, NOT REQUIREMENTS. A button "
        "appearing here is not evidence that anyone asked for it to be "
        "tested. Never create a checkpoint for something only because it "
        "appears in this list; every checkpoint must still come from the "
        "document."
    )
