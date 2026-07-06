"""Video walkthrough ingestion (Phase 5) — Gemini multimodal digest.

Turns an uploaded design-walkthrough video into the same checkpoint schema
the SOW pipeline produces (see design_ingest.py), cached in the Memory Bank
so each video is digested exactly once — this is the highest-token operation
on the platform, which is why caching and hard size caps matter here.

Talks to the Gemini API directly over REST (stdlib urllib) rather than
through litellm: video ingestion needs the Gemini Files API (resumable
upload + processing poll), which is provider-specific by design — the
architecture assigns video digestion to Gemini ("The Brain").

Flow:
  1. Resumable upload to generativelanguage.googleapis.com (Files API)
  2. Poll until the file state is ACTIVE (video preprocessing server-side)
  3. generateContent with file_uri + extraction prompt (JSON response mode)
  4. Delete the remote file (courtesy cleanup of the free-tier file quota)

Model chain: VISUAL_VIDEO_MODEL (default gemini-3.5-flash) then
VISUAL_VIDEO_FALLBACK (default gemini-2.5-flash), with bounded retries on
rate limits — mirroring the router's policy for this Gemini-only path.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.logging import get_logger
from app.services.design_ingest import IngestError, _validate_checkpoint

logger = get_logger(__name__)

_API_BASE = "https://generativelanguage.googleapis.com"
_UPLOAD_TIMEOUT_S = 300      # large body upload
_REQUEST_TIMEOUT_S = 180     # generateContent over a long video takes a while
_POLL_INTERVAL_S = 5
_POLL_MAX_S = 300            # give up if video preprocessing exceeds 5 minutes
_MAX_RETRIES = 2
_BACKOFF_BASE_S = 5.0

_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
}

_VIDEO_SYSTEM_PROMPT = (
    "You are a senior QA analyst watching a design walkthrough video of a web "
    "application. Extract every concrete, testable requirement shown or "
    "narrated as a checkpoint. Respond with JSON only:\n"
    '{"checkpoints": [{"type": "functional"|"visual", "title": str, '
    '"description": str, "page": str|null, "expected": str|null}]}\n'
    "Rules: 'description' must be an imperative, testable goal. Use type "
    "'visual' for layout/branding/design requirements and 'functional' for "
    "behavior. Note the page/screen each checkpoint applies to when visible. "
    "Do NOT invent requirements not shown in the video. Return "
    '{"checkpoints": []} if none are found.'
)


def _api_key() -> str:
    """Resolve the Gemini key with the same precedence llm_router uses."""
    for var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    plural = os.environ.get("GOOGLE_API_KEYS", "")
    first = plural.split(",")[0].strip() if plural.strip() else ""
    if first:
        return first
    raise IngestError(
        "No Gemini key configured. Set GEMINI_API_KEY (or GOOGLE_API_KEY) to "
        "enable video ingestion."
    )


def mime_for(file_name: str) -> str:
    ext = os.path.splitext(file_name.lower())[1]
    mime = _MIME_BY_EXT.get(ext)
    if not mime:
        raise IngestError(
            f"Unsupported video format '{ext}'. Use .mp4, .webm, or .mov."
        )
    return mime


def _request(
    url: str,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict | None = None,
    timeout_s: int = _REQUEST_TIMEOUT_S,
):
    """Perform an HTTP request, returning (status, headers, body bytes)."""
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        return resp.status, dict(resp.headers), resp.read()


# ── Files API ────────────────────────────────────────────────────────────────

def _upload_video(path: str, mime_type: str, key: str) -> dict:
    """Resumable upload; returns the Gemini file resource dict."""
    size = os.path.getsize(path)

    # Step 1: start the resumable session
    start_url = f"{_API_BASE}/upload/v1beta/files?key={urllib.parse.quote(key)}"
    meta = json.dumps({"file": {"display_name": os.path.basename(path)}}).encode()
    try:
        _, resp_headers, _ = _request(
            start_url,
            method="POST",
            body=meta,
            headers={
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": mime_type,
                "Content-Type": "application/json",
            },
            timeout_s=60,
        )
    except urllib.error.HTTPError as exc:
        raise IngestError(f"Gemini upload could not start (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise IngestError(f"Could not reach the Gemini API: {exc.reason}") from exc

    upload_url = resp_headers.get("X-Goog-Upload-URL") or resp_headers.get(
        "x-goog-upload-url"
    )
    if not upload_url:
        raise IngestError("Gemini upload session did not return an upload URL.")

    # Step 2: send the bytes and finalize in one shot
    with open(path, "rb") as fh:
        content = fh.read()
    try:
        _, _, body = _request(
            upload_url,
            method="POST",
            body=content,
            headers={
                "X-Goog-Upload-Command": "upload, finalize",
                "X-Goog-Upload-Offset": "0",
                "Content-Length": str(size),
            },
            timeout_s=_UPLOAD_TIMEOUT_S,
        )
    except urllib.error.HTTPError as exc:
        raise IngestError(f"Gemini upload failed (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise IngestError(f"Gemini upload failed: {exc.reason}") from exc

    try:
        file_info = json.loads(body.decode("utf-8")).get("file") or {}
    except json.JSONDecodeError as exc:
        raise IngestError("Gemini upload returned an unreadable response.") from exc
    if not file_info.get("uri"):
        raise IngestError("Gemini upload did not return a file URI.")
    return file_info


def _wait_until_active(file_name: str, key: str) -> None:
    """Poll the file resource until video preprocessing finishes."""
    url = f"{_API_BASE}/v1beta/{file_name}?key={urllib.parse.quote(key)}"
    deadline = time.monotonic() + _POLL_MAX_S
    while time.monotonic() < deadline:
        try:
            _, _, body = _request(url, timeout_s=30)
            state = json.loads(body.decode("utf-8")).get("state", "")
        except (urllib.error.URLError, json.JSONDecodeError, OSError):
            state = ""  # transient — keep polling until the deadline
        if state == "ACTIVE":
            return
        if state == "FAILED":
            raise IngestError("Gemini could not process this video (state FAILED).")
        time.sleep(_POLL_INTERVAL_S)
    raise IngestError("Timed out waiting for Gemini to process the video.")


def _delete_file(file_name: str, key: str) -> None:
    """Best-effort remote cleanup — failure here must never fail the ingest."""
    url = f"{_API_BASE}/v1beta/{file_name}?key={urllib.parse.quote(key)}"
    try:
        _request(url, method="DELETE", timeout_s=30)
    except Exception:  # noqa: BLE001
        logger.warning("Video ingest: could not delete remote file %s", file_name)


# ── Digest ───────────────────────────────────────────────────────────────────

def _generate(model: str, file_uri: str, mime_type: str, key: str) -> str:
    """One generateContent call; returns the raw text response."""
    url = (
        f"{_API_BASE}/v1beta/models/{urllib.parse.quote(model)}:generateContent"
        f"?key={urllib.parse.quote(key)}"
    )
    payload = json.dumps(
        {
            "contents": [
                {
                    "parts": [
                        {"file_data": {"file_uri": file_uri, "mime_type": mime_type}},
                        {"text": _VIDEO_SYSTEM_PROMPT},
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            },
        }
    ).encode()
    _, _, body = _request(
        url,
        method="POST",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    data = json.loads(body.decode("utf-8"))
    candidates = data.get("candidates") or []
    if not candidates:
        raise IngestError("Gemini returned no candidates for the video digest.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise IngestError("Gemini returned an empty video digest.")
    return text


def digest_video(storage_path: str, file_name: str) -> tuple[list[dict], str]:
    """Full pipeline: upload → wait ACTIVE → digest → cleanup.

    Returns (checkpoints, model_used). Raises IngestError on failure.
    """
    mime_type = mime_for(file_name)
    key = _api_key()

    primary = os.environ.get("VISUAL_VIDEO_MODEL", "").strip() or "gemini-3.5-flash"
    fallback = os.environ.get("VISUAL_VIDEO_FALLBACK", "").strip() or "gemini-2.5-flash"
    models = [primary] + ([fallback] if fallback != primary else [])

    file_info = _upload_video(storage_path, mime_type, key)
    remote_name = file_info.get("name", "")
    file_uri = file_info["uri"]
    logger.info("Video ingest: uploaded %s as %s", file_name, remote_name)

    try:
        if file_info.get("state") != "ACTIVE":
            _wait_until_active(remote_name, key)

        text: str | None = None
        model_used: str = models[0]
        last_error: Exception | None = None
        for model in models:
            for attempt in range(1, _MAX_RETRIES + 2):
                try:
                    text = _generate(model, file_uri, mime_type, key)
                    model_used = model
                    break
                except urllib.error.HTTPError as exc:
                    last_error = exc
                    # 429/5xx: retry same model with backoff; other 4xx: next model
                    if (exc.code == 429 or exc.code >= 500) and attempt <= _MAX_RETRIES:
                        wait = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                        logger.warning(
                            "Video ingest: HTTP %d from %s, retrying in %.0fs",
                            exc.code, model, wait,
                        )
                        time.sleep(wait)
                        continue
                    logger.warning(
                        "Video ingest: %s failed (HTTP %d), trying next model",
                        model, exc.code,
                    )
                    break
                except (urllib.error.URLError, json.JSONDecodeError, OSError, IngestError) as exc:
                    last_error = exc
                    logger.warning("Video ingest: %s errored: %s", model, exc)
                    break
            if text is not None:
                break
        if text is None:
            raise IngestError(f"All Gemini models failed for the video digest: {last_error}")

        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestError("Gemini video digest was not valid JSON.") from exc

        items = raw.get("checkpoints", []) if isinstance(raw, dict) else []
        checkpoints = [c for c in (_validate_checkpoint(i) for i in items) if c]
        dropped = len(items) - len(checkpoints)
        if dropped:
            logger.warning("Video ingest: dropped %d schema-invalid checkpoint(s)", dropped)
        logger.info(
            "Video ingest: %d checkpoint(s) extracted from %s via %s",
            len(checkpoints), file_name, model_used,
        )
        return checkpoints, model_used
    finally:
        if remote_name:
            _delete_file(remote_name, key)
