"""Figma REST API client (Phase 4b) — list and export frames as PNGs.

Uses only the Python stdlib (urllib) — deliberately no new HTTP dependency,
since browser-use pins parts of the httpx dependency tree and reliability of
the existing install matters more than ergonomics here.

Auth: FIGMA_API_TOKEN env var (a personal access token, read-only scope is
sufficient). Never accepted from the client, never logged.

Figma free tier notes:
  * REST API is available on the free plan for files you can view.
  * Image export renders server-side and can take a few seconds per frame —
    which is why downloads happen in a Celery task, not in the API request.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from app.core.logging import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.figma.com/v1"
_TIMEOUT_S = 30
_EXPORT_SCALE = 2  # 2x for crisper pixel-diff comparisons
_MAX_FRAMES_PER_IMPORT = 20


class FigmaError(RuntimeError):
    """User-safe Figma failure (bad token, missing file, API error)."""


def _token() -> str:
    token = os.environ.get("FIGMA_API_TOKEN", "").strip()
    if not token:
        raise FigmaError(
            "FIGMA_API_TOKEN is not configured on the server. Add it to the "
            "backend environment to enable Figma imports."
        )
    return token


def _get(path: str, params: dict | None = None) -> dict:
    """GET a Figma API endpoint, returning parsed JSON. Raises FigmaError."""
    url = f"{_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"X-Figma-Token": _token()})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise FigmaError("Figma rejected the token (403). Check FIGMA_API_TOKEN.") from exc
        if exc.code == 404:
            raise FigmaError("Figma file not found — check the file key/URL and token access.") from exc
        if exc.code == 429:
            raise FigmaError("Figma rate limit hit (429). Try again in a minute.") from exc
        raise FigmaError(f"Figma API error (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise FigmaError(f"Could not reach the Figma API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise FigmaError("Figma API returned an unreadable response.") from exc


def parse_file_key(file_key_or_url: str) -> str:
    """Accept a raw file key or a full Figma URL and return the file key.

    Handles both URL shapes: figma.com/file/<key>/... and
    figma.com/design/<key>/...
    """
    value = file_key_or_url.strip()
    match = re.search(r"figma\.com/(?:file|design)/([A-Za-z0-9]+)", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9]+", value):
        return value
    raise FigmaError("Provide a Figma file URL or its file key.")


def list_frames(file_key: str) -> list[dict]:
    """List top-level frames: [{node_id, name, page}].

    depth=2 returns document → pages → top-level children only, keeping the
    payload small even for huge files.
    """
    data = _get(f"/files/{file_key}", {"depth": 2})
    frames: list[dict] = []
    document = data.get("document") or {}
    for page in document.get("children") or []:
        page_name = page.get("name", "")
        for node in page.get("children") or []:
            if node.get("type") == "FRAME":
                frames.append(
                    {
                        "node_id": node.get("id", ""),
                        "name": node.get("name", "")[:200],
                        "page": page_name[:200],
                    }
                )
    logger.info("Figma: listed %d frame(s) in file %s", len(frames), file_key)
    return frames


def export_frames(file_key: str, node_ids: list[str]) -> dict[str, str]:
    """Request PNG renders for node_ids. Returns {node_id: image_url}.

    Figma renders asynchronously server-side; the returned URLs are
    short-lived S3 links (entries can be null if a render failed).
    """
    if not node_ids:
        return {}
    if len(node_ids) > _MAX_FRAMES_PER_IMPORT:
        raise FigmaError(f"Import at most {_MAX_FRAMES_PER_IMPORT} frames at a time.")
    data = _get(
        f"/images/{file_key}",
        {"ids": ",".join(node_ids), "format": "png", "scale": _EXPORT_SCALE},
    )
    if data.get("err"):
        raise FigmaError(f"Figma export failed: {data['err']}")
    return {k: v for k, v in (data.get("images") or {}).items() if v}


def download_png(image_url: str) -> bytes:
    """Download a rendered PNG from Figma's short-lived URL."""
    request = urllib.request.Request(image_url)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S * 2) as resp:
            content = resp.read()
    except (urllib.error.URLError, OSError) as exc:
        raise FigmaError(f"Could not download rendered frame: {exc}") from exc
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise FigmaError("Figma returned a non-PNG render.")
    return content
