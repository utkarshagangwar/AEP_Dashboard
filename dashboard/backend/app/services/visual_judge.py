"""The Judge — visual audit engine (Phase 2).

Two independent passes over (reference design image, live screenshot):

  1. Pixel-diff (deterministic, authoritative for color/spacing):
     exact per-pixel comparison via `pixelmatch`, mismatch regions clustered
     into bounding boxes with exact expected/actual hex values sampled from
     each region's center. Runs even when all LLM providers are down, so the
     platform always produces at least a deterministic report.

  2. Vision (AI, authoritative for structure/missing elements):
     both images are sent through the LLM router (llm_router.complete) with a
     strict JSON schema. Invalid output → one repair retry inside the router →
     otherwise the vision pass is marked unavailable (never fabricated).

Merge policy: pixel-diff findings win for color/spacing properties; vision
findings are kept for structural issues only, and any vision finding that
contradicts a deterministic measurement is dropped.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# Ignore anti-aliasing noise; tune via env without code change.
_DEFAULT_THRESHOLD = 0.1          # pixelmatch color-distance threshold (0..1)
_DEFAULT_MIN_REGION_PX = 64       # ignore mismatch clusters smaller than this
_DEFAULT_FAIL_PCT = 1.0           # run "fails" if mismatch % exceeds this

# Self-execution (orchestrator "Brain" requirement): when pixel-diff is
# already conclusive — near-identical or overwhelmingly different — the
# vision-model pass adds no value, so it's skipped to save a model call.
# Applies to every judge() call, not just orchestrated ones (see judge()).
_DEFAULT_SELF_EXEC_LOW_PCT = 0.5    # <= this mismatch % => "near-zero", skip
_DEFAULT_SELF_EXEC_HIGH_PCT = 60.0  # >= this mismatch % => "overwhelming", skip

_VISION_SYSTEM = (
    "You are a meticulous visual QA engineer. Compare the FIRST image "
    "(expected design) with the SECOND image (live screenshot). Report only "
    "STRUCTURAL differences: missing/extra elements, wrong text, wrong element "
    "order or position category (e.g. button moved from header to footer). Do "
    "NOT report exact colors, pixel spacing, or font-size guesses — a "
    "deterministic tool handles those. Respond with JSON only: "
    '{"findings": [{"element": str, "issue": str, "expected": str, '
    '"actual": str, "severity": "critical"|"major"|"minor"|"info"}]} '
    "Return {\"findings\": []} if there are no structural differences."
)

# Properties the deterministic pass owns; vision findings mentioning these
# are dropped in the merge step.
_DETERMINISTIC_KEYWORDS = ("color", "colour", "hex", "spacing", "padding",
                           "margin", "px", "pixel")


@dataclass
class JudgeVerdict:
    """Combined result of both passes."""

    pixel_mismatch_pct: float
    diff_image_path: Optional[str]
    findings: list[dict] = field(default_factory=list)  # rows for VisualFinding
    vision_available: bool = True
    vision_model: Optional[str] = None
    summary: str = ""
    vision_skipped: bool = False
    vision_skip_reason: Optional[str] = None


# ── Pass 1: deterministic pixel diff ─────────────────────────────────────────

def _rgb_to_hex(px) -> str:
    return "#{:02X}{:02X}{:02X}".format(px[0], px[1], px[2])


def _describe_region(region: dict) -> tuple[str, str]:
    """Turn a {x_pct,y_pct,w_pct,h_pct} bounding box into a plain-language
    (location, size) pair for non-technical readers, e.g. ("the top-right of
    the page", "a small area") instead of raw percentages.
    """
    cx = region["x_pct"] + region["w_pct"] / 2
    cy = region["y_pct"] + region["h_pct"] / 2
    vert = "top" if cy < 33 else "bottom" if cy > 66 else "middle"
    horiz = "left" if cx < 33 else "right" if cx > 66 else "center"
    if vert == "middle" and horiz == "center":
        location = "the center of the page"
    elif vert == "middle":
        location = f"the {horiz} side of the page"
    elif horiz == "center":
        location = f"the {vert} of the page"
    else:
        location = f"the {vert}-{horiz} of the page"

    area_pct = region["w_pct"] * region["h_pct"] / 100
    size = (
        "most of the page" if area_pct > 40 else
        "a large area" if area_pct > 15 else
        "a noticeable area" if area_pct > 3 else
        "a small area"
    )
    return location, size


def _cluster_regions(mask, width: int, height: int, min_px: int) -> list[dict]:
    """Group mismatched pixels into rectangular regions via a coarse grid scan.

    A full connected-component pass on large screenshots is overkill here; a
    32px grid gives stable, human-reviewable boxes and is dependency-free.
    """
    grid = 32
    cells = {}
    for y, x in mask:
        cells.setdefault((y // grid, x // grid), []).append((y, x))

    regions: list[dict] = []
    visited = set()
    for cell in cells:
        if cell in visited:
            continue
        # BFS over adjacent grid cells to merge touching clusters
        stack, members = [cell], []
        while stack:
            c = stack.pop()
            if c in visited or c not in cells:
                continue
            visited.add(c)
            members.extend(cells[c])
            cy, cx = c
            stack.extend(
                (cy + dy, cx + dx)
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dy, dx) != (0, 0)
            )
        if len(members) < min_px:
            continue
        ys = [p[0] for p in members]
        xs = [p[1] for p in members]
        mid_y = (min(ys) + max(ys)) / 2
        mid_x = (min(xs) + max(xs)) / 2
        # Sample an actual mismatched pixel closest to the bbox midpoint —
        # NOT the raw midpoint itself, which can land on a pixel that happens
        # to match (irregular diff shapes), producing a confusing
        # "Expected: #FFF · Actual: #FFF" for a region flagged as differing.
        sample_y, sample_x = min(
            members, key=lambda p: (p[0] - mid_y) ** 2 + (p[1] - mid_x) ** 2
        )
        regions.append(
            {
                "x_pct": round(min(xs) / width * 100, 2),
                "y_pct": round(min(ys) / height * 100, 2),
                "w_pct": round((max(xs) - min(xs) + 1) / width * 100, 2),
                "h_pct": round((max(ys) - min(ys) + 1) / height * 100, 2),
                "sample_point": (sample_y, sample_x),
                "pixel_count": len(members),
            }
        )
    # Largest regions first; cap to keep reports readable
    regions.sort(key=lambda r: r["pixel_count"], reverse=True)
    return regions[:25]


def run_pixel_diff(
    reference_path: str,
    screenshot_path: str,
    diff_output_path: str,
) -> tuple[float, list[dict]]:
    """Deterministic diff. Returns (mismatch_pct, findings).

    Images are size-normalized (screenshot scaled to the reference's
    dimensions) so a viewport rounding difference doesn't flag the whole page.
    """
    from PIL import Image
    from pixelmatch.contrib.PIL import pixelmatch

    threshold = float(os.environ.get("VISUAL_DIFF_THRESHOLD", _DEFAULT_THRESHOLD))
    min_region = int(os.environ.get("VISUAL_DIFF_MIN_REGION_PX", _DEFAULT_MIN_REGION_PX))

    ref = Image.open(reference_path).convert("RGBA")
    shot = Image.open(screenshot_path).convert("RGBA")
    if shot.size != ref.size:
        logger.info(
            "Visual judge: resizing screenshot %s -> %s for diff", shot.size, ref.size
        )
        shot = shot.resize(ref.size, Image.LANCZOS)

    width, height = ref.size
    diff_img = Image.new("RGBA", ref.size)
    mismatch = pixelmatch(
        ref, shot, diff_img, threshold=threshold, includeAA=False
    )
    diff_img.save(diff_output_path)
    mismatch_pct = round(mismatch / (width * height) * 100, 2)

    # Locate mismatch pixels from the diff overlay (pixelmatch paints them red)
    mask = []
    diff_px = diff_img.load()
    for y in range(height):
        for x in range(width):
            p = diff_px[x, y]
            if p[3] > 0 and p[0] > 200 and p[1] < 100:  # red-ish marker
                mask.append((y, x))

    ref_px = ref.load()
    shot_px = shot.load()
    findings = []
    for region in _cluster_regions(mask, width, height, min_region):
        sy, sx = region.pop("sample_point")
        expected_hex = _rgb_to_hex(ref_px[sx, sy])
        actual_hex = _rgb_to_hex(shot_px[sx, sy])
        region.pop("pixel_count", None)
        location, size = _describe_region(region)
        findings.append(
            {
                "engine": "pixel_diff",
                "severity": "major" if region["w_pct"] * region["h_pct"] > 1 else "minor",
                "element": None,
                "issue": (
                    f"The design and the live page don't match in {size} near "
                    f"{location} ({region['w_pct']}% x {region['h_pct']}% of the viewport)."
                ),
                "expected": expected_hex,
                "actual": actual_hex,
                "region": region,
            }
        )
    return mismatch_pct, findings


# ── Pass 2: AI vision (structural) ───────────────────────────────────────────

def run_vision_pass(
    reference_path: str,
    screenshot_path: str,
    model_override: Optional[str] = None,
) -> tuple[list[dict], Optional[str]]:
    """Structural comparison via the LLM router. Returns (findings, model_used).

    Raises nothing to the caller on provider failure — returns ([], None) and
    the run is marked "partial" upstream, preserving the deterministic report.

    model_override: passed straight through to llm_router.complete() — see
    its docstring. None (the default) preserves today's static-chain
    behavior; the orchestrator sets this to steer which model runs this pass.
    """
    from app.services import llm_router

    try:
        result = llm_router.complete(
            "Compare these two images per your instructions.",
            system=_VISION_SYSTEM,
            images_b64=[
                llm_router.encode_image_file(reference_path),
                llm_router.encode_image_file(screenshot_path),
            ],
            expect_json=True,
            model_override=model_override,
        )
    except Exception as exc:  # noqa: BLE001 — vision pass must never kill the run
        logger.warning("Visual judge: vision pass unavailable: %s", exc)
        return [], None

    raw = result.parsed_json or {}
    findings = []
    valid_severities = {"critical", "major", "minor", "info"}
    for item in raw.get("findings", []):
        if not isinstance(item, dict) or not item.get("issue"):
            continue  # schema-invalid entry — drop, never guess
        issue_text = str(item.get("issue", "")).lower()
        # Merge policy: deterministic pass owns color/spacing claims
        if any(kw in issue_text for kw in _DETERMINISTIC_KEYWORDS):
            continue
        findings.append(
            {
                "engine": "vision",
                "severity": item.get("severity") if item.get("severity") in valid_severities else "minor",
                "element": str(item.get("element") or "")[:500] or None,
                "issue": str(item["issue"]),
                "expected": str(item.get("expected") or "") or None,
                "actual": str(item.get("actual") or "") or None,
                "region": None,
            }
        )
    return findings, result.model_used


# ── Combined entry point ─────────────────────────────────────────────────────

def judge(
    reference_path: str,
    screenshot_path: str,
    diff_output_path: str,
    model_override: Optional[str] = None,
) -> JudgeVerdict:
    """Run both passes and merge into a single verdict.

    model_override: passed through to run_vision_pass() when the vision pass
    actually runs — lets the orchestrator steer which model the Judge uses.
    None (the default) preserves today's static-chain behavior.

    Self-execution: when VISUAL_JUDGE_SELF_EXEC_ENABLED (default true), the
    vision pass is skipped entirely if pixel-diff mismatch is already
    conclusive (near-zero or overwhelming) — the Brain deciding it doesn't
    need to delegate to a model call because the deterministic pass already
    answered the question. This applies to every judge() call, including the
    existing standalone POST /api/v1/visual-audits path, not just
    orchestrated runs — intentional per the confirmed requirement.
    """
    mismatch_pct, pixel_findings = run_pixel_diff(
        reference_path, screenshot_path, diff_output_path
    )

    self_exec_enabled = os.environ.get(
        "VISUAL_JUDGE_SELF_EXEC_ENABLED", "true"
    ).strip().lower() not in ("false", "0", "no")
    low_pct = float(os.environ.get("VISUAL_SELF_EXEC_LOW_PCT", _DEFAULT_SELF_EXEC_LOW_PCT))
    high_pct = float(os.environ.get("VISUAL_SELF_EXEC_HIGH_PCT", _DEFAULT_SELF_EXEC_HIGH_PCT))

    vision_skipped = False
    vision_skip_reason: Optional[str] = None
    if self_exec_enabled and (mismatch_pct <= low_pct or mismatch_pct >= high_pct):
        vision_findings, vision_model = [], None
        vision_skipped = True
        vision_skip_reason = (
            f"Pixel-diff mismatch {mismatch_pct}% is "
            f"{'near-zero' if mismatch_pct <= low_pct else 'overwhelming'} — "
            "vision pass skipped as conclusive without a model call."
        )
        logger.info("Visual judge: self-execution skip — %s", vision_skip_reason)
    else:
        vision_findings, vision_model = run_vision_pass(
            reference_path, screenshot_path, model_override=model_override
        )

    findings = pixel_findings + vision_findings
    fail_pct = float(os.environ.get("VISUAL_FAIL_MISMATCH_PCT", _DEFAULT_FAIL_PCT))

    parts = [f"Pixel mismatch: {mismatch_pct}% (threshold {fail_pct}%)."]
    parts.append(f"{len(pixel_findings)} deterministic finding(s).")
    if vision_skipped:
        parts.append(vision_skip_reason)
    elif vision_model:
        parts.append(f"{len(vision_findings)} structural finding(s) via {vision_model}.")
    else:
        parts.append("Vision pass unavailable — deterministic results only.")

    return JudgeVerdict(
        pixel_mismatch_pct=mismatch_pct,
        diff_image_path=diff_output_path,
        findings=findings,
        vision_available=vision_model is not None,
        vision_model=vision_model,
        summary=" ".join(parts),
        vision_skipped=vision_skipped,
        vision_skip_reason=vision_skip_reason,
    )
