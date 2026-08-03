"""Phase 1 wiring — the public extraction entry points still behave.

Guards the two properties that matter after doc_blocks was introduced
beneath design_ingest and sow_import:

  1. design_ingest.extract_text() output is UNCHANGED for .txt/.md/.pdf.
     An earlier draft routed it through doc_blocks.render_blocks(), which
     silently stripped markdown heading markers and list bullets out of the
     SOW Checkpoints prompt. These tests exist so that cannot recur.
  2. Extension gates did not widen. design_ingest must still refuse .docx;
     only sow_import accepts it.
"""
from __future__ import annotations

import pytest

from app.services import design_ingest, doc_blocks, sow_import


# ── extract_text output is byte-identical to the pre-Phase-1 behaviour ───────

def _legacy_extract_text(storage_path: str, file_name: str) -> str:
    """Verbatim copy of the pre-Phase-1 design_ingest.extract_text() body."""
    import os

    ext = os.path.splitext(file_name.lower())[1]
    if ext in (".txt", ".md"):
        with open(storage_path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    elif ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(storage_path)
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    else:
        raise AssertionError(ext)
    return text.strip()


@pytest.mark.parametrize("name", ["numbered.md", "flat.txt", "multipage.pdf"])
def test_extract_text_is_byte_identical_to_legacy(fixture_path, name):
    path = fixture_path(name)
    assert design_ingest.extract_text(path, name) == _legacy_extract_text(path, name)


def test_extract_text_preserves_markdown_heading_markers(fixture_path):
    """The specific regression: '#' markers must survive to the LLM prompt."""
    text = design_ingest.extract_text(fixture_path("numbered.md"), "numbered.md")
    assert "## 1. Scope of Work" in text
    assert "### 1.1 Candidate List" in text
    assert "- Free-text search box" in text


# ── extension gates unchanged ────────────────────────────────────────────────

def test_design_ingest_still_refuses_docx(fixture_path):
    """The SOW Checkpoints pipeline has never accepted .docx. Adding block
    extraction beneath it must not have widened that contract."""
    with pytest.raises(doc_blocks.IngestError) as excinfo:
        design_ingest.extract_blocks(fixture_path("structured.docx"), "structured.docx")
    assert "Use .txt, .md, or .pdf" in str(excinfo.value)


def test_sow_import_accepts_all_four_formats():
    assert set(sow_import.SUPPORTED_EXTENSIONS) == {".docx", ".pdf", ".txt", ".md"}


def test_sow_import_refuses_unsupported(tmp_path):
    path = tmp_path / "a.rtf"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(doc_blocks.IngestError) as excinfo:
        sow_import.extract_existing_sow_blocks(str(path), "a.rtf")
    assert "Use .docx, .pdf, .txt, or .md" in str(excinfo.value)


# ── IngestError identity ─────────────────────────────────────────────────────

def test_ingest_error_identity_preserved_across_modules():
    """Every existing `except design_ingest.IngestError` must still catch
    errors raised inside doc_blocks -- they have to be the same class."""
    assert design_ingest.IngestError is doc_blocks.IngestError
    assert sow_import.IngestError is doc_blocks.IngestError


def test_sow_ledger_import_of_ingest_error_still_works():
    """sow_ledger does `from app.services.design_ingest import IngestError`."""
    from app.services.sow_ledger import IngestError as from_ledger

    assert from_ledger is doc_blocks.IngestError


# ── sow_import text path ─────────────────────────────────────────────────────

def test_sow_import_delegates_text_formats_unchanged(fixture_path):
    """.txt/.md/.pdf must go through design_ingest.extract_text, not the
    lossy renderer."""
    for name in ("numbered.md", "flat.txt", "multipage.pdf"):
        path = fixture_path(name)
        assert sow_import.extract_existing_sow_text(path, name) == (
            design_ingest.extract_text(path, name)
        )


def test_sow_import_docx_text_is_rendered_in_document_order(fixture_path):
    text = sow_import.extract_existing_sow_text(
        fixture_path("structured.docx"), "structured.docx"
    )
    assert text.index("REQ-001") < text.index("3. Sign-off")


def test_max_sow_chars_single_source_of_truth():
    assert sow_import._MAX_SOW_CHARS == doc_blocks.MAX_SOW_CHARS
