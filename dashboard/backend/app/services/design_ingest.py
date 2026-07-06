"""The Brain — design/SOW ingestion (Phase 3).

Turns an uploaded SOW document (txt / md / pdf) into structured visual &
functional checkpoints via the LLM router, and caches the result in the
Memory Bank (design_rules) keyed by the artifact's sha256 so each document
is ever parsed once.

Checkpoint schema stored in design_rules.checkpoints (JSONB):
  [
    {
      "type": "functional" | "visual",
      "title": str,           # short label
      "description": str,     # what to verify, phrased as a testable goal
      "page": str | null,     # page/screen it applies to, if stated
      "expected": str | null  # explicit expected value/behavior, if stated
    },
    ...
  ]

Reliability rules:
  * Text extraction failures and LLM failures surface as parse_status="error"
    with a human-readable parse_error — never a silent empty result.
  * LLM output is schema-validated entry by entry; invalid entries are
    dropped, never repaired by guessing.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_SOW_CHARS = 60_000  # ~15k tokens — generous for an SOW, guards runaway PDFs

_SOW_SYSTEM = (
    "You are a senior QA analyst. You will receive the text of a Statement of "
    "Work / requirements document for a web application. Extract every "
    "concrete, testable requirement as a checkpoint. Respond with JSON only:\n"
    '{"checkpoints": [{"type": "functional"|"visual", "title": str, '
    '"description": str, "page": str|null, "expected": str|null}]}\n'
    "Rules: 'description' must be phrased as an imperative, testable goal "
    "(e.g. 'Verify the checkout page shows a summary of cart items'). Use "
    "type 'visual' for layout/branding/design requirements and 'functional' "
    "for behavior. Do NOT invent requirements that are not in the document. "
    'Return {"checkpoints": []} if none are found.'
)


class IngestError(RuntimeError):
    """Raised when a document cannot be ingested; message is user-safe."""


# ── Text extraction ──────────────────────────────────────────────────────────

def extract_text(storage_path: str, file_name: str) -> str:
    """Extract plain text from a SOW file. Supports .txt, .md, .pdf."""
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
        logger.warning(
            "SOW ingest: document truncated from %d to %d chars", len(text), _MAX_SOW_CHARS
        )
        text = text[:_MAX_SOW_CHARS]
    return text


# ── Checkpoint extraction via the router ─────────────────────────────────────

def _validate_checkpoint(item: object) -> dict | None:
    """Return a normalized checkpoint dict, or None if schema-invalid."""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    description = str(item.get("description") or "").strip()
    ctype = item.get("type")
    if not description or ctype not in ("functional", "visual"):
        return None
    return {
        "type": ctype,
        "title": (title or description[:80])[:200],
        "description": description[:2000],
        "page": (str(item["page"]).strip()[:500] or None) if item.get("page") else None,
        "expected": (str(item["expected"]).strip()[:2000] or None) if item.get("expected") else None,
    }


def parse_sow(text: str) -> tuple[list[dict], str]:
    """Extract checkpoints from SOW text. Returns (checkpoints, model_used).

    Raises IngestError on total provider failure (caller marks the artifact
    as errored; a later retry can re-enqueue).
    """
    from app.services import llm_router

    try:
        result = llm_router.complete(
            "Extract QA checkpoints from this document:\n\n" + text,
            system=_SOW_SYSTEM,
            expect_json=True,
            max_tokens=8192,
        )
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
