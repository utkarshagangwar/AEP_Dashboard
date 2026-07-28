"""Live capture for AI Vibe Test runs — CDP screencast fan-out.

Opens a second CDP session on the page ai_runner.py already has open
(alongside Playwright's own and browser-use's use of that same page — CDP
allows multiple simultaneous sessions/domains on one target, so this never
interferes with the agent driving the page) and uses Page.startScreencast to
receive JPEG frames as Chromium repaints. Each accepted frame is fanned out
two ways:
  1. Published on a Redis pub/sub channel so the FastAPI process (a
     separate container from this Celery worker) can relay it to the
     browser over an SSE endpoint for a genuinely live view.
  2. Piped into an ffmpeg subprocess (image2pipe -> H.264 mp4) to produce
     the full-session recording, written to the shared visual_qa_data
     volume.

Best-effort throughout: any failure here (ffmpeg missing, Redis
unreachable, CDP screencast unsupported) is logged and swallowed — this is
an auxiliary feature and must never fail or slow down the underlying test
run. Frames are throttled client-side to ~_TARGET_FPS regardless of how
fast Chromium actually repaints, to bound bandwidth/CPU/video size.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_TARGET_FPS = 6
_MIN_FRAME_INTERVAL_S = 1.0 / _TARGET_FPS
_SCREENCAST_MAX_WIDTH = 1280
_SCREENCAST_MAX_HEIGHT = 800
_SCREENCAST_QUALITY = 55
_FFMPEG_STOP_TIMEOUT_S = 20


def _video_dir() -> str:
    # Same VISUAL_DATA_DIR env var / default already used by
    # app/workers/tasks/visual_audit.py — same shared volume, new subfolder.
    base = os.environ.get("VISUAL_DATA_DIR", os.path.join(os.getcwd(), "visual_qa_data"))
    path = os.path.join(base, "ai_run_videos")
    os.makedirs(path, exist_ok=True)
    return path


def video_path_for(run_id: str) -> str:
    return os.path.join(_video_dir(), f"{run_id}.mp4")


class _CaptureSession:
    def __init__(self, run_id: str, video_path: str):
        self.run_id = run_id
        self.video_path = video_path
        self.channel = f"ai_run_frames:{run_id}"
        self.cdp = None
        self.redis_client = None
        self.ffmpeg_proc: Optional["asyncio.subprocess.Process"] = None
        self._last_frame_ts = 0.0

    def _on_screencast_frame(self, params: dict) -> None:
        # Playwright dispatches CDP event handlers synchronously; hand off
        # to the running loop so ffmpeg/Redis I/O never blocks the next
        # frame's delivery (or the ack that keeps frames flowing).
        asyncio.create_task(self._handle_frame(params))

    async def _handle_frame(self, params: dict) -> None:
        session_id = params.get("sessionId")
        try:
            now = time.monotonic()
            if now - self._last_frame_ts < _MIN_FRAME_INTERVAL_S:
                return
            self._last_frame_ts = now

            data_b64 = params.get("data")
            if not data_b64:
                return

            if self.ffmpeg_proc is not None and self.ffmpeg_proc.stdin is not None:
                try:
                    self.ffmpeg_proc.stdin.write(base64.b64decode(data_b64))
                    await self.ffmpeg_proc.stdin.drain()
                except Exception:
                    logger.debug(
                        "Live capture: ffmpeg write failed for run %s", self.run_id, exc_info=True
                    )

            if self.redis_client is not None:
                try:
                    await self.redis_client.publish(self.channel, json.dumps({"jpg": data_b64}))
                except Exception:
                    logger.debug(
                        "Live capture: Redis publish failed for run %s", self.run_id, exc_info=True
                    )
        except Exception:
            logger.debug(
                "Live capture: frame handling failed for run %s", self.run_id, exc_info=True
            )
        finally:
            # Chromium stops sending further frames until the previous one
            # is acked — always ack, even a throttled/dropped frame, so the
            # screencast never stalls.
            if self.cdp is not None and session_id is not None:
                try:
                    await self.cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
                except Exception:
                    pass


async def start_capture(page, run_id: str) -> Optional[_CaptureSession]:
    """Best-effort: start CDP screencast + Redis publisher + ffmpeg encoder
    for `run_id`. Returns None (never raises) on any failure — the caller
    must proceed with the test run regardless of whether this succeeded."""
    session = _CaptureSession(run_id, video_path_for(run_id))
    try:
        try:
            import redis.asyncio as redis_asyncio

            from app.core.config import settings

            session.redis_client = redis_asyncio.from_url(settings.CELERY_BROKER_URL)
        except Exception:
            logger.warning(
                "Live capture: Redis unavailable for run %s (live view disabled)",
                run_id, exc_info=True,
            )
            session.redis_client = None

        try:
            session.ffmpeg_proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "image2pipe",
                "-framerate", str(_TARGET_FPS),
                "-i", "-",
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                session.video_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            logger.warning(
                "Live capture: ffmpeg unavailable for run %s (recording disabled)",
                run_id, exc_info=True,
            )
            session.ffmpeg_proc = None

        if session.redis_client is None and session.ffmpeg_proc is None:
            # Neither sink is usable — no point paying for a screencast.
            return None

        session.cdp = await page.context.new_cdp_session(page)
        session.cdp.on("Page.screencastFrame", session._on_screencast_frame)
        await session.cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": _SCREENCAST_QUALITY,
                "maxWidth": _SCREENCAST_MAX_WIDTH,
                "maxHeight": _SCREENCAST_MAX_HEIGHT,
                "everyNthFrame": 1,
            },
        )
        return session
    except Exception:
        logger.warning(
            "Live capture: failed to start for run %s — continuing without it",
            run_id, exc_info=True,
        )
        await stop_capture(session)
        return None


async def stop_capture(session: Optional[_CaptureSession]) -> Optional[str]:
    """Stop screencast/publisher/encoder and return the finished video's
    path, or None if nothing usable was produced. Never raises."""
    if session is None:
        return None

    if session.cdp is not None:
        try:
            await session.cdp.send("Page.stopScreencast")
        except Exception:
            pass

    if session.redis_client is not None:
        try:
            await session.redis_client.aclose()
        except Exception:
            pass

    video_path = None
    if session.ffmpeg_proc is not None:
        try:
            if session.ffmpeg_proc.stdin is not None:
                session.ffmpeg_proc.stdin.close()
            # Bounded wait so a wedged ffmpeg can never hang run teardown —
            # awaited (not killed outright) so the moov atom for
            # -movflags +faststart is written correctly on the normal path.
            await asyncio.wait_for(session.ffmpeg_proc.wait(), timeout=_FFMPEG_STOP_TIMEOUT_S)
        except Exception:
            logger.warning(
                "Live capture: ffmpeg did not exit cleanly for run %s",
                session.run_id, exc_info=True,
            )
            try:
                session.ffmpeg_proc.kill()
            except Exception:
                pass
        else:
            try:
                if os.path.isfile(session.video_path) and os.path.getsize(session.video_path) > 1024:
                    video_path = session.video_path
            except OSError:
                pass

    return video_path
