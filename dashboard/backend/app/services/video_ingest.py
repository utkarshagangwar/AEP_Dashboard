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

import base64
import json
import os
import subprocess
import tempfile
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
    # .mkv is NOT sent to Gemini as-is — the Files API's supported video list
    # has no Matroska type, so an upload declaring video/x-matroska is
    # rejected. Every .mkv is converted to .mp4 by _prepare_for_upload()
    # before it reaches _upload_video(), and this entry exists only so
    # mime_for() accepts the extension rather than raising first. See
    # _remux_to_mp4 for why webm's mime is not reused instead.
    ".mkv": "video/x-matroska",
}

# Extensions Gemini cannot ingest directly and that must be converted first.
_NEEDS_CONVERSION_EXTS = (".mkv",)


def _build_video_prompt(platform_name: str) -> str:
    """Build the video-digest system prompt, anchored to the user-declared
    platform name so the model has a fact to check against instead of
    inferring/assuming what product it's looking at.

    platform_name is mandatory (enforced at the API layer) precisely because
    an unanchored model asked to "watch a video of a web application" will,
    on an ambiguous or low-signal recording, fill the gap with whatever
    product identity it can find the strongest signal for.

    Identity-checking is a TOP-LEVEL field (platform_match), never a fake
    checkpoint — a mismatch or an empty result must be able to fail the
    ingestion outright (see digest_video) rather than flow through
    _validate_checkpoint and get silently saved as a runnable skill. Before
    that flag is trusted, the model is forced to enumerate every piece of
    on-screen/audio brand evidence it noticed (brand_evidence) — a chrome
    element like a header/sidebar logo is easy to miss if the model only
    weighs the main content panel, and a wrong "mismatch" verdict is exactly
    as bad as a wrong "match" verdict, so listing evidence first is required
    precision, not decoration.
    """
    return (
        "You are a senior QA analyst watching a screen-recording walkthrough "
        f"of a web application called '{platform_name}' (per the uploader). "
        "You have NOT read any requirements document for this product and "
        "know nothing about it beyond what this video literally shows or "
        "says, plus the name you were just given. Respond with JSON only, "
        "in exactly this shape:\n"
        '{"brand_evidence": [str], "platform_match": bool, '
        '"mismatch_evidence": str|null, "checkpoints": [{"type": '
        '"functional"|"visual", "title": str, "description": str|null, '
        '"role": str|null, "objective": str|null, "context": str|null, '
        '"instructions": [str]|null, "notes": [str]|null, "page": str|null, '
        '"expected": str|null}]}\n'
        "\n"
        "STEP 1 — brand_evidence (do this before anything else): watch the "
        "ENTIRE video, including parts you might normally skim, and scan "
        "EVERY region of every distinct screen — not just the main/central "
        "content panel. Explicitly check the header bar, top-left and "
        "top-right corners, side navigation, logo/favicon, footer, and "
        "browser tab title, in addition to the body content, since a "
        "product's own branding is very often a small, persistent logo or "
        "header label while the main panel shows one specific feature or "
        "internal tool screen. List every distinct product name, logo text, "
        "or brand mark you found anywhere on screen or spoken in narration, "
        f"each as a short string noting roughly where you saw it, e.g. "
        f"[\"top-left sidebar header reads '{platform_name}'\", \"page "
        "title in the browser tab reads 'Dashboard'\"]. If you genuinely "
        "saw no legible branding anywhere (e.g. a blank/loading screen "
        "throughout), say so explicitly as one entry — do not leave this "
        "array empty without explanation.\n"
        "\n"
        "STEP 2 — platform_match: based ONLY on brand_evidence above, is "
        f"this recording OF '{platform_name}'? Set this true if any entry in "
        f"brand_evidence plausibly names or matches '{platform_name}' "
        "(exact name, abbreviation/acronym of it, or an obvious close "
        "variant/misspelling) ANYWHERE — even briefly, even small, even if "
        "the main content area is showing one specific internal feature, "
        "tool, admin screen, or settings page. A feature/tab/tool name "
        "shown in the content area (e.g. an internal dashboard section, "
        "settings page, or admin tool) is NOT a mismatch by itself — it's "
        "normal for a product walkthrough to spend most of its time inside "
        "one feature of that product. Set platform_match to FALSE only if, "
        "after the full scan above, brand_evidence contains NO plausible "
        f"reference to '{platform_name}' anywhere AND instead shows clear, "
        "specific branding for a different, unrelated product. When "
        "genuinely uncertain after a careful scan, prefer TRUE over FALSE — "
        "a false 'mismatch' throws away a legitimately analyzable video, "
        "which is a worse outcome than proceeding.\n"
        "\n"
        "If platform_match is false: set 'mismatch_evidence' to a precise "
        "sentence naming the different product/content you saw instead "
        "(referencing specific brand_evidence entries), and return "
        '"checkpoints": [] — do not extract checkpoints from unrelated '
        "content.\n"
        "\n"
        "If platform_match is true: set 'mismatch_evidence' to null and "
        "extract every concrete, testable requirement shown or narrated as "
        "a checkpoint, per the rules below.\n"
        "\n"
        "GROUNDING RULE for checkpoints (most important — read before "
        "writing any checkpoint): every checkpoint must point back to a "
        "specific moment in THIS RECORDING — an on-screen button/label/"
        "field/page title you can literally see, or narration you can "
        "literally hear. Never describe the product's general business "
        "purpose, audience, architecture, or 'the platform consists of...' "
        "style claims unless that exact wording appears on-screen or is "
        "spoken out loud in this video. Do not fill gaps using outside/"
        "prior knowledge of what a product with this name or type "
        "'usually' has — if you recognize the application from training "
        "knowledge, IGNORE that knowledge entirely and describe only what "
        "this specific recording shows. If a particular moment is unclear "
        "or you are not confident a requirement was actually demonstrated "
        "at that moment, leave that one out rather than guessing — this is "
        "independent of the platform_match decision above, which is about "
        "product identity, not about how much detail any one moment shows.\n"
        "\n"
        "For type \"visual\" (layout/branding/design requirements): fill only "
        "'description' — a short, testable claim about appearance you actually "
        "observed on screen. Leave role/objective/context/instructions/notes "
        "null.\n"
        "\n"
        "For type \"functional\" (behavior/workflow requirements): leave "
        "'description' null and instead fill these structured fields, based "
        "strictly on what the video shows — a browser-automation AI agent will "
        "execute this skill with no extra context, so be concrete and complete:\n"
        "  'role': the persona/preconditions for this test, e.g. \"Logged in as "
        "an admin user,\" only if that login/state is actually shown.\n"
        "  'objective': one sentence defining what a PASS looks like, describing "
        "the on-screen outcome you saw.\n"
        "  'context': the starting page/state shown in the video (exact page "
        "name/title/URL if visible).\n"
        "  'instructions': an ORDERED array of atomic, imperative steps (one "
        "action per string) reproducing the exact clicks/inputs/navigation seen "
        "in the recording, e.g. [\"Click the 'Create Job' button.\", \"Enter "
        "'QA Engineer' into the Job Title field.\", \"Click Submit.\"]. Use the "
        "exact field labels and button text visible in the video. Do NOT invent "
        "steps, fields, or requirements not shown in the video.\n"
        "  'notes': an array of caveats, edge cases, or expected values worth "
        "flagging, taken only from what's visible/audible (can be empty).\n"
        "\n"
        "'title' is a short (3-6 word) label for the functionality, e.g. "
        "\"Job Creation\", \"Add Candidate\". Note the page/screen each "
        "checkpoint applies to when visible.\n"
        "\n"
        'Return "checkpoints": [] if platform_match is true but the video '
        "still does not clearly demonstrate any testable requirement — an "
        "empty result is correct and expected for a video with too little "
        "content, rather than inventing plausible-sounding ones."
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
            f"Unsupported video format '{ext}'. Use .mp4, .webm, .mov, or .mkv."
        )
    return mime


# ── Matroska (.mkv) conversion ───────────────────────────────────────────────
#
# WHY THIS EXISTS. Gemini's Files API accepts mp4/mpeg/mov/avi/flv/mpg/webm/
# wmv/3gpp. Matroska is not on that list, so a .mkv upload fails at the API
# boundary no matter what mime we declare.
#
# WHY NOT JUST CALL IT video/webm. WebM *is* a Matroska profile, so the
# container would parse — but WebM permits only VP8/VP9/AV1 video and
# Vorbis/Opus audio, while a .mkv in the wild (OBS's default recording
# format, most desktop screen recorders) almost always carries H.264 or
# HEVC. Mislabelling it means Gemini either errors mid-decode or, worse,
# silently reads a partial stream and digests an incomplete walkthrough.
# Converting is the only option that behaves the same for every .mkv.
#
# COST. The common case is a stream copy: the H.264/AAC elementary streams
# are moved into an MP4 container untouched, no re-encode, seconds even for
# a 500MB file. Only when the codecs cannot live in MP4 (e.g. VP9 or Opus
# audio) do we fall back to a real transcode, which is slow but correct.
#
# ffmpeg is a hard requirement for .mkv only. It is installed in
# docker/Dockerfile.backend; everywhere else in this module ffmpeg is
# best-effort (still frames), so the failure here must be an explicit
# IngestError rather than a silent degrade — a .mkv that cannot be converted
# genuinely cannot be digested.

_REMUX_TIMEOUT_S = 900  # a full transcode of a long walkthrough is not quick


def _run_ffmpeg(args: list[str]) -> bool:
    """Run ffmpeg, returning True on a clean exit. Never raises."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", *args],
            capture_output=True, timeout=_REMUX_TIMEOUT_S, check=True,
        )
        return True
    except FileNotFoundError:
        logger.error("Video ingest: ffmpeg is not installed — cannot convert .mkv")
        return False
    except subprocess.TimeoutExpired:
        logger.error("Video ingest: ffmpeg timed out after %ss", _REMUX_TIMEOUT_S)
        return False
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()[-500:]
        logger.warning("Video ingest: ffmpeg failed: %s", stderr)
        return False


def _convert_to_mp4(path: str) -> str:
    """Convert a Matroska file to MP4 and return the new temp path.

    Tries a stream copy first (fast, lossless); falls back to an H.264/AAC
    transcode. The caller owns the returned path and must delete it.
    Raises IngestError if both attempts fail.
    """
    fd, out_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)  # ffmpeg writes the file itself; we only wanted a unique name

    # +faststart moves the moov atom to the front. Gemini fetches the file
    # server-side and a trailing moov forces it to read the whole thing before
    # it can decode anything — same reason ai_run_capture.py sets it.
    copy_args = ["-i", path, "-c", "copy", "-movflags", "+faststart", out_path]
    if _run_ffmpeg(copy_args) and os.path.getsize(out_path) > 0:
        logger.info("Video ingest: remuxed %s to MP4 by stream copy", path)
        return out_path

    logger.info("Video ingest: stream copy failed for %s, transcoding", path)
    transcode_args = [
        "-i", path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        out_path,
    ]
    if _run_ffmpeg(transcode_args) and os.path.getsize(out_path) > 0:
        logger.info("Video ingest: transcoded %s to MP4", path)
        return out_path

    try:
        os.remove(out_path)
    except OSError:
        pass
    raise IngestError(
        "Could not convert this .mkv file to MP4 for analysis. The file may be "
        "corrupt, or ffmpeg is unavailable on the server. Re-save the "
        "walkthrough as .mp4 and upload it again."
    )


def _prepare_for_upload(
    storage_path: str, file_name: str, mime_type: str
) -> tuple[str, str, str | None]:
    """Resolve (path_to_upload, mime_type, temp_path_to_clean_up).

    Formats Gemini accepts natively pass straight through with no temp file
    (third element is None). .mkv is converted to MP4 first.

    mime_type is passed in rather than looked up here because two callers
    with different format vocabularies share this: digest_video (video only)
    and sow_ledger.extract_ledger_from_recording (video *and* audio).
    """
    ext = os.path.splitext(file_name.lower())[1]
    if ext not in _NEEDS_CONVERSION_EXTS:
        return storage_path, mime_type, None
    converted = _convert_to_mp4(storage_path)
    return converted, "video/mp4", converted


# ── Still-frame extraction (precision assist) ───────────────────────────────
#
# Gemini's native video understanding reliably under-attends small, persistent
# UI chrome — a header logo or sidebar brand label present on every frame can
# still be missed even with mediaResolution=HIGH, because attention is
# dominated by the "main content" narrative of the video rather than static
# peripheral text (observed directly: a large, high-contrast "AEP" sidebar
# label was missed twice in a row even after being told explicitly to check
# corners/logos and even at high resolution). A handful of plain still images
# extracted from the same video and handed to the model as ordinary images —
# which multimodal models read far more reliably than a compressed video
# frame — closes that gap. Best-effort throughout: any failure here degrades
# precision, it must never fail the ingestion (video-only analysis is the
# fallback, not a hard dependency on ffmpeg being present).

_FRAME_TIMESTAMP_FRACTIONS = (0.05, 0.35, 0.65, 0.95)
_FRAME_FALLBACK_OFFSETS_S = (1.0, 5.0, 15.0)
_FFMPEG_TIMEOUT_S = 30


def _ffprobe_duration(path: str) -> float | None:
    """Best-effort video duration in seconds; None if ffprobe is unavailable
    or the file can't be probed."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_S, check=True,
        )
        return float(result.stdout.strip())
    except Exception:  # noqa: BLE001 — ffprobe missing/failing must never break ingestion
        return None


def _extract_still_frames(path: str) -> list[bytes]:
    """Extract a handful of JPEG stills spread across the video's timeline.
    Returns [] (never raises) if ffmpeg is unavailable or every extraction
    attempt fails — callers must treat stills as an optional precision boost."""
    duration = _ffprobe_duration(path)
    if duration and duration > 0:
        offsets = [round(duration * f, 2) for f in _FRAME_TIMESTAMP_FRACTIONS]
    else:
        offsets = list(_FRAME_FALLBACK_OFFSETS_S)

    frames: list[bytes] = []
    for offset in offsets:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", str(offset), "-i", path,
                    "-frames:v", "1", "-q:v", "3", tmp_path,
                ],
                capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=True,
            )
            with open(tmp_path, "rb") as fh:
                data = fh.read()
            if data:
                frames.append(data)
        except Exception:  # noqa: BLE001
            logger.warning("Video ingest: could not extract still frame at %ss", offset)
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    return frames


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

def _generate(
    model: str,
    file_uri: str,
    mime_type: str,
    key: str,
    prompt: str,
    still_frames: list[bytes],
) -> str:
    """One generateContent call; returns the raw text response."""
    url = (
        f"{_API_BASE}/v1beta/models/{urllib.parse.quote(model)}:generateContent"
        f"?key={urllib.parse.quote(key)}"
    )
    parts: list[dict] = [{"file_data": {"file_uri": file_uri, "mime_type": mime_type}}]
    if still_frames:
        parts.append({
            "text": (
                f"The {len(still_frames)} image(s) below are plain still frames "
                "captured directly from the SAME video above, spread across its "
                "timeline. They are provided in addition to the video specifically "
                "so you can read small on-screen text/branding (headers, sidebars, "
                "logos, corners) with full confidence — compressed video frames can "
                "make a persistent UI element hard to make out even though it's "
                "visible on every frame. Inspect these images carefully for any "
                "product name, logo, or brand text before writing brand_evidence; "
                "do not limit your brand check to only the main video."
            )
        })
        for frame_bytes in still_frames:
            parts.append({
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": base64.b64encode(frame_bytes).decode("ascii"),
                }
            })
    parts.append({"text": prompt})
    payload = json.dumps(
        {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": 0.0,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                # Default video frame resolution (~70 tokens/frame) is tuned
                # for scene-level understanding and is not enough to reliably
                # read small, persistent UI chrome (a sidebar logo, a header
                # brand label) — exactly the text platform_match depends on.
                # HIGH (~280 tokens/frame) is Gemini's documented setting for
                # reading dense/small on-screen text. Costs ~4x the tokens
                # per frame, accepted deliberately: a wrong platform_match
                # verdict is worse than the extra cost.
                "mediaResolution": "MEDIA_RESOLUTION_HIGH",
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


def digest_video(storage_path: str, file_name: str, platform_name: str) -> tuple[list[dict], str]:
    """Full pipeline: upload → wait ACTIVE → digest → cleanup.

    platform_name is the user-declared product this video walks through
    (mandatory — see _build_video_prompt for why). Returns (checkpoints,
    model_used). Raises IngestError on failure.
    """
    key = _api_key()
    prompt = _build_video_prompt(platform_name)

    # .mkv is converted to MP4 here; every other format passes straight
    # through. upload_path/temp_path are the same file for a converted video,
    # and temp_path is None otherwise — the finally block below is what
    # guarantees the conversion never leaks a temp file on any exit path.
    upload_path, mime_type, temp_path = _prepare_for_upload(
        storage_path, file_name, mime_for(file_name)
    )

    try:
        still_frames = _extract_still_frames(upload_path)
        logger.info(
            "Video ingest: extracted %d still frame(s) from %s for brand/text precision",
            len(still_frames), file_name,
        )

        primary = os.environ.get("VISUAL_VIDEO_MODEL", "").strip() or "gemini-3.5-flash"
        fallback = os.environ.get("VISUAL_VIDEO_FALLBACK", "").strip() or "gemini-2.5-flash"
        models = [primary] + ([fallback] if fallback != primary else [])

        file_info = _upload_video(upload_path, mime_type, key)
        remote_name = file_info.get("name", "")
        file_uri = file_info["uri"]
        logger.info("Video ingest: uploaded %s as %s", file_name, remote_name)
    except BaseException:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise

    try:
        if file_info.get("state") != "ACTIVE":
            _wait_until_active(remote_name, key)

        text: str | None = None
        model_used: str = models[0]
        last_error: Exception | None = None
        for model in models:
            for attempt in range(1, _MAX_RETRIES + 2):
                try:
                    text = _generate(model, file_uri, mime_type, key, prompt, still_frames)
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
        if not isinstance(raw, dict):
            raise IngestError("Gemini video digest returned an unexpected shape.")

        brand_evidence = raw.get("brand_evidence") or []
        logger.info(
            "Video ingest: %s brand evidence for %s: %s",
            file_name, platform_name, brand_evidence,
        )

        # platform_match is a hard gate, not a checkpoint: a mismatch (or a
        # missing/malformed field from a non-compliant model response) must
        # fail ingestion outright — parse_status=error, no DesignRule, no
        # skill ever saved — rather than flow through _validate_checkpoint
        # as a fake "finding" a user could accidentally run as a skill.
        if raw.get("platform_match") is False:
            evidence = raw.get("mismatch_evidence") or "no further detail returned."
            evidence_str = "; ".join(str(e) for e in brand_evidence) or "none found"
            raise IngestError(
                f"This video does not appear to show '{platform_name}': {evidence} "
                f"(on-screen evidence considered: {evidence_str}). "
                "Re-record/re-upload a walkthrough of the correct platform, or correct "
                "the declared platform name and re-upload."
            )

        items = raw.get("checkpoints", []) if isinstance(raw, dict) else []
        checkpoints = [c for c in (_validate_checkpoint(i) for i in items) if c]
        dropped = len(items) - len(checkpoints)
        if dropped:
            logger.warning("Video ingest: dropped %d schema-invalid checkpoint(s)", dropped)
        if not checkpoints:
            raise IngestError(
                "No testable requirements could be extracted from this video — the "
                "recording may be too short, unclear, or not show enough concrete "
                "UI interaction. Re-record a more detailed walkthrough and try again."
            )
        # A walkthrough only ever demonstrates success, so the digest above
        # can only produce happy paths. classify_and_expand categorises what
        # was observed and derives the negative/edge cases that category
        # requires, all labelled grounding="derived" — the strict
        # "only what the recording shows" rule above is untouched, and
        # nothing derived is ever presented as observed. It also runs the same
        # coverage backstop and variant cap the SOW path gets, so a behaviour
        # missing a variant its category requires is flagged and re-requested
        # here too rather than held to a quietly lower standard. It never
        # raises: a video that digested successfully is not failed by
        # enrichment.
        from app.services import tdd_extraction

        checkpoints, expansion_model = tdd_extraction.classify_and_expand(checkpoints)
        if expansion_model and expansion_model not in model_used:
            model_used = f"{model_used}, {expansion_model}"

        logger.info(
            "Video ingest: %d checkpoint(s) extracted from %s via %s",
            len(checkpoints), file_name, model_used,
        )
        return checkpoints, model_used
    finally:
        if remote_name:
            _delete_file(remote_name, key)
        # The converted MP4 is scratch space, not the artifact — the original
        # .mkv stays at storage_path and remains the file of record.
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                logger.warning("Video ingest: could not remove temp file %s", temp_path)
