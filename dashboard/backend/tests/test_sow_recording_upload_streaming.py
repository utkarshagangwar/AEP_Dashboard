"""The meeting-recording upload must stream to disk, not buffer in memory.

Bug this locks down: add_recording_source did

    content = await file.read()          # whole body into RAM
    if len(content) > max_bytes: 413     # ...checked only afterwards

so at the 300MB default cap a single upload cost 300MB of resident memory,
and an OVER-cap upload was fully resident by the time it was rejected -- the
cap protected storage but not the process. A handful of concurrent uploads
could OOM the API container, and the rejection path was the worst case
rather than the cheapest one.

It now reads in fixed chunks, hashes as it goes, and raises 413 the moment
the running total passes the cap. These tests assert the three properties
that actually matter and that a future refactor could silently lose:

  1. the body is never requested in one unbounded read,
  2. an over-cap upload is cut off early rather than fully consumed,
  3. no temp file survives the rejection.

The suite has no database and no HTTP client by design (see conftest.py), so
this drives the endpoint function directly and stops at the size check --
which is before any DB access on the failure path.
"""
from __future__ import annotations

import asyncio
import glob
import hashlib
import os
import tempfile

import pytest
from fastapi import HTTPException

from app.api.v1 import sow


class RecordingUploadFile:
    """UploadFile stand-in that reports how it was read.

    `reads` records the size argument of every read() call, so a test can
    prove the endpoint asked for bounded chunks rather than the whole body.
    `served` counts the bytes actually handed over, which is what shows an
    over-cap upload was abandoned early instead of fully consumed.
    """

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data
        self._pos = 0
        self.reads: list[int | None] = []
        self.served = 0

    async def read(self, size: int = -1) -> bytes:
        self.reads.append(size if size != -1 else None)
        if size is None or size < 0:
            chunk = self._data[self._pos:]
        else:
            chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        self.served += len(chunk)
        return chunk


@pytest.fixture
def enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("SOW_ENABLED", "true")
    monkeypatch.setenv("VISUAL_DATA_DIR", str(tmp_path))
    # The route carries @limiter.limit("10/hour"); SlowAPI's wrapper demands a
    # real starlette Request it can key on. Rate limiting is not what these
    # tests are about, so switch it off rather than fabricate an ASGI scope.
    from app.core.rate_limit import limiter

    monkeypatch.setattr(limiter, "enabled", False)
    # The document lookup is DB-backed; the streaming code under test runs
    # before any query, so a stub document is enough to reach it.
    monkeypatch.setattr(
        sow, "_get_active_document_or_404", lambda db, doc_id: object()
    )
    return tmp_path


def call(upload, *, max_mb: str = "1"):
    """Invoke the endpoint far enough to exercise the streaming loop."""
    os.environ["SOW_MAX_RECORDING_MB"] = max_mb
    return asyncio.run(
        sow.add_recording_source(
            document_id="doc-1",
            request=None,
            file=upload,
            context_label=None,
            db=None,
            current_user=None,
        )
    )


def temp_files_matching(ext: str) -> list[str]:
    return glob.glob(os.path.join(tempfile.gettempdir(), f"*{ext}"))


# ── The bug ──────────────────────────────────────────────────────────────────

def test_body_is_never_read_in_one_unbounded_call(enabled):
    """`await file.read()` with no argument is the regression. Every read
    must carry an explicit bound."""
    upload = RecordingUploadFile("meeting.mp4", b"x" * (3 * 1024 * 1024))

    with pytest.raises(HTTPException):
        call(upload, max_mb="1")

    assert upload.reads, "the body was never read at all"
    assert all(
        r is not None and r > 0 for r in upload.reads
    ), f"unbounded read of the whole body: {upload.reads}"


def test_oversized_upload_is_cut_off_instead_of_fully_consumed(enabled):
    """The cap has to bite during the transfer. If the endpoint still drains
    the whole body first, `served` equals the full size and the memory
    problem is back even though the status code looks right."""
    size = 5 * 1024 * 1024
    upload = RecordingUploadFile("meeting.mp4", b"x" * size)

    with pytest.raises(HTTPException) as exc:
        call(upload, max_mb="1")

    assert exc.value.status_code == 413
    assert upload.served < size, "the entire oversized body was read into memory"
    # One chunk of slack past the cap is expected: the total is checked after
    # the read that crosses it.
    assert upload.served <= 1024 * 1024 + sow._UPLOAD_CHUNK_BYTES


def test_rejected_upload_leaves_no_temp_file_behind(enabled):
    before = set(temp_files_matching(".mp4"))
    upload = RecordingUploadFile("meeting.mp4", b"x" * (4 * 1024 * 1024))

    with pytest.raises(HTTPException):
        call(upload, max_mb="1")

    leaked = set(temp_files_matching(".mp4")) - before
    assert not leaked, f"413 path leaked temp file(s): {leaked}"


def test_empty_upload_is_rejected_as_400_not_413(enabled):
    upload = RecordingUploadFile("meeting.mp4", b"")

    with pytest.raises(HTTPException) as exc:
        call(upload, max_mb="1")

    assert exc.value.status_code == 400
    assert "empty" in exc.value.detail.lower()


# ── Properties the streaming rewrite must preserve ───────────────────────────

def test_chunked_hash_matches_the_whole_file_hash(enabled, monkeypatch):
    """Dedupe is by sha256. Hashing incrementally must produce exactly what
    hashing the buffered bytes did, or every existing artifact stops matching
    and the same recording is re-analysed (and re-billed) on every upload."""
    data = bytes(range(256)) * 5000
    expected = hashlib.sha256(data).hexdigest()

    seen = {}

    class CapturingDb:
        def query(self, *a, **k):
            raise _StopHere(seen["sha"])

    class _StopHere(Exception):
        pass

    # Capture the digest at the moment the dedupe query would run.
    real_sha256 = hashlib.sha256

    class TrackingSha:
        def __init__(self):
            self._h = real_sha256()

        def update(self, b):
            self._h.update(b)

        def hexdigest(self):
            seen["sha"] = self._h.hexdigest()
            return seen["sha"]

    monkeypatch.setattr(sow.hashlib, "sha256", lambda *a: TrackingSha())

    upload = RecordingUploadFile("meeting.mp4", data)
    os.environ["SOW_MAX_RECORDING_MB"] = "100"
    with pytest.raises(_StopHere):
        asyncio.run(
            sow.add_recording_source(
                document_id="doc-1",
                request=None,
                file=upload,
                context_label=None,
                db=CapturingDb(),
                current_user=None,
            )
        )

    assert seen["sha"] == expected
    assert upload.served == len(data), "a valid upload must be read in full"


def test_extension_is_carried_onto_the_temp_file(enabled, monkeypatch):
    """ffprobe infers the container from the suffix, so the temp file the
    duration check runs against must keep the upload's extension."""
    captured = {}
    real_mkstemp = tempfile.mkstemp

    def spy(*args, **kwargs):
        captured["suffix"] = kwargs.get("suffix")
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(sow.tempfile, "mkstemp", spy)

    upload = RecordingUploadFile("Team Sync.MKV", b"x" * (2 * 1024 * 1024))
    with pytest.raises(HTTPException):
        call(upload, max_mb="1")

    assert captured["suffix"] == ".mkv"
