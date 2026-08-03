"""Regression: a reused artifact whose file is missing from storage.

Bug: uploads are deduplicated by sha256. When a DesignArtifact row already
existed, the endpoint reused it and skipped writing the file, trusting the
database about the state of the filesystem. If the visual_qa_data volume was
recreated or pruned while Postgres survived, the row pointed at a path that
no longer existed. The upload returned 201 and the failure surfaced later in
the Celery worker as:

    Could not read document: [Errno 2] No such file or directory:
    '/app/visual_qa_data/sow_existing_document/<sha>.md'

Re-uploading could not fix it, because re-uploading took the same dedup
branch that skipped the write.

These tests exercise _ensure_artifact_file directly with stubs -- the unit
suite has no database and no HTTP client by design (see tests/conftest.py).
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from app.api.v1.sow import _ensure_artifact_file


class StubDb:
    """Minimal Session stand-in: records whether the row was persisted."""

    def __init__(self):
        self.commits = 0
        self.refreshes = 0

    def commit(self):
        self.commits += 1

    def refresh(self, _obj):
        self.refreshes += 1


def make_artifact(storage_path, sha="abc123"):
    return SimpleNamespace(id="artifact-1", sha256=sha, storage_path=storage_path)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("VISUAL_DATA_DIR", str(tmp_path))
    return tmp_path


# ── The bug ──────────────────────────────────────────────────────────────────

def test_missing_file_is_rewritten_from_uploaded_content(data_dir):
    """The reported failure: row exists, file does not."""
    missing = str(data_dir / "sow_existing_document" / "abc123.md")
    artifact = make_artifact(missing)
    db = StubDb()

    assert not os.path.exists(missing)
    _ensure_artifact_file(
        db, artifact, b"# Restored SOW\n", subdir="sow_existing_document", ext=".md"
    )

    assert os.path.exists(missing), "the missing file was not restored"
    with open(missing, "rb") as fh:
        assert fh.read() == b"# Restored SOW\n"


def test_restored_file_is_readable_by_the_extractor(data_dir):
    """End of the chain: after healing, the worker's extraction path works.
    This is the assertion that actually proves the reported error is gone."""
    from app.services.sow_import import extract_existing_sow_blocks

    missing = str(data_dir / "sow_existing_document" / "abc123.md")
    _ensure_artifact_file(
        StubDb(),
        make_artifact(missing),
        b"# Scope of Work\n\nThe portal has a bulk delete button.\n",
        subdir="sow_existing_document",
        ext=".md",
    )

    blocks = extract_existing_sow_blocks(missing, "IG 2.5.2 - Main AI (SOW).md")
    assert any(b["kind"] == "heading" and b["text"] == "Scope of Work" for b in blocks)


def test_row_is_repointed_when_the_directory_drifted(data_dir):
    """A path under an old/stale data dir is rewritten under the current one."""
    artifact = make_artifact("/gone/old_volume/sow_existing_document/abc123.md")
    db = StubDb()

    _ensure_artifact_file(
        db, artifact, b"content", subdir="sow_existing_document", ext=".md"
    )

    expected = str(data_dir / "sow_existing_document" / "abc123.md")
    assert artifact.storage_path == expected
    assert os.path.exists(expected)
    assert db.commits == 1, "the repointed path was not persisted"


def test_null_storage_path_is_handled(data_dir):
    artifact = make_artifact(None, sha="def456")
    _ensure_artifact_file(
        StubDb(), artifact, b"content", subdir="sow_existing_document", ext=".md"
    )
    assert artifact.storage_path.endswith("def456.md")
    assert os.path.exists(artifact.storage_path)


# ── No behaviour change on the healthy path ──────────────────────────────────

def test_existing_file_is_left_completely_alone(data_dir):
    """The hot path must not gain a write, a commit, or a content change."""
    path = data_dir / "sow_existing_document" / "abc123.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original content")

    artifact = make_artifact(str(path))
    db = StubDb()

    _ensure_artifact_file(
        db, artifact, b"DIFFERENT content", subdir="sow_existing_document", ext=".md"
    )

    assert path.read_bytes() == b"original content", "healthy file was overwritten"
    assert db.commits == 0, "healthy path performed an unnecessary commit"
    assert artifact.storage_path == str(path)


def test_original_extension_is_preserved_on_restore(data_dir):
    """Content is byte-identical across extensions, but extension drives
    parser selection downstream -- restoring must not silently change it."""
    artifact = make_artifact(
        str(data_dir / "sow_existing_document" / "abc123.txt")
    )
    _ensure_artifact_file(
        StubDb(), artifact, b"content", subdir="sow_existing_document", ext=".md"
    )
    assert artifact.storage_path.endswith(".txt")


# ── Failure surfaces as a clean error, not a raw 500 ─────────────────────────

def test_unwritable_storage_raises_user_safe_http_error(data_dir, monkeypatch):
    from fastapi import HTTPException

    def boom(*_a, **_kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(os, "makedirs", boom)

    with pytest.raises(HTTPException) as excinfo:
        _ensure_artifact_file(
            StubDb(),
            make_artifact(str(data_dir / "sow_existing_document" / "abc123.md")),
            b"content",
            subdir="sow_existing_document",
            ext=".md",
        )

    assert excinfo.value.status_code == 500
    assert "visual_qa_data" in excinfo.value.detail
    assert "Traceback" not in excinfo.value.detail


# ── Every sha-dedup upload path is covered ───────────────────────────────────

def test_all_artifact_reuse_branches_call_the_guard():
    """Structural guard.

    Four SOW upload endpoints deduplicate by sha256 and reuse an existing
    DesignArtifact row. Every one of them must verify the file still exists,
    or it reintroduces the ENOENT-in-the-worker bug for its own source type.
    A new upload endpoint added without the guard should fail this test.
    """
    import inspect

    from app.api.v1 import sow

    source = inspect.getsource(sow)

    expected_subdirs = {
        "sow_existing_document",   # add_existing_sow_source
        "sow_meeting_transcript",  # add_transcript_source
        "sow_meeting_recording",   # add_recording_source
        "sow_design_ref",          # add_design_source (upload branch only)
    }
    for subdir in expected_subdirs:
        assert f'subdir="{subdir}"' in source, (
            f"upload path for {subdir!r} does not call _ensure_artifact_file"
        )

    # One guarded reuse branch per sha-dedup write site.
    assert source.count("_ensure_artifact_file(") == len(expected_subdirs) + 1  # +1 = the def
