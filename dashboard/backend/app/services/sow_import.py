"""Import SOW (SOW tab) — text extraction for an uploaded pre-existing SOW.

Feeds app.services.sow_ledger's extract_ledger_from_sow_document* (which in
turn feeds app.workers.tasks.sow_ledger::extract_existing_sow_ledger_task):
a user attaches an already-written SOW/requirements document as a source on
a sow_documents row, this module turns it into plain text, and the ledger
extraction described in SOW_FEATURE_PLAN.md §2 Pass 1 takes it from there —
same downstream contract every other source type (transcript/recording/
design) already uses.

Deliberately a separate module from app.services.design_ingest, not an
extension of it: design_ingest.extract_text() is reused UNMODIFIED here for
.txt/.md/.pdf (identical extraction need, zero reason to fork it), but this
module adds .docx support, which design_ingest has never needed (the
SOW-Checkpoints pipeline it serves has only ever accepted txt/md/pdf) —
adding it there would be an unrelated behavior change to an existing,
already-shipped endpoint. Keeping it here means that pipeline's behavior is
untouched.

Reliability rule, same as design_ingest/sow_ledger: extraction failures
raise IngestError with a user-safe message — never a silent empty result.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger
from app.services import doc_blocks
from app.services.doc_blocks import Block, IngestError

logger = get_logger(__name__)

# The size ceiling is now enforced in exactly one place
# (doc_blocks.MAX_SOW_CHARS) instead of being duplicated here and in
# design_ingest. Re-exported so any caller reading sow_import._MAX_SOW_CHARS
# still sees the live value rather than a copy that can drift out of sync.
_MAX_SOW_CHARS = doc_blocks.MAX_SOW_CHARS

_DOCX_EXTENSIONS = doc_blocks.DOCX_EXTENSIONS
_DELEGATED_EXTENSIONS = doc_blocks.TEXT_EXTENSIONS + doc_blocks.PDF_EXTENSIONS
SUPPORTED_EXTENSIONS = _DOCX_EXTENSIONS + _DELEGATED_EXTENSIONS


def extract_existing_sow_blocks(storage_path: str, file_name: str) -> list[Block]:
    """Structured blocks for an uploaded existing-SOW file (SOW_CHUNKING_PLAN
    Phase 1). Supports .docx, .pdf, .txt, .md — the same format set the
    "Import SOW" upload endpoint (app/api/v1/sow.py) validates against.

    This is what app.services.doc_chunking consumes. Heading levels, table
    boundaries and page markers are preserved, which is what lets the
    chunker split on real section boundaries instead of counting characters.

    The .docx parsing that used to live in this module's _extract_docx_text()
    now lives in doc_blocks, because the .pdf/.md paths need the same block
    model and duplicating it would have guaranteed the two drifted apart.
    """
    ext = os.path.splitext(file_name.lower())[1]
    if ext not in SUPPORTED_EXTENSIONS:
        raise IngestError(
            f"Unsupported SOW format '{ext}'. Use .docx, .pdf, .txt, or .md."
        )
    return doc_blocks.extract_blocks(storage_path, file_name)


def extract_existing_sow_text(storage_path: str, file_name: str) -> str:
    """Plain text from an uploaded existing-SOW file, rendered from the
    structured blocks above.

    BEHAVIOR CHANGE (SOW_CHUNKING_PLAN Phase 1, deliberate): .docx tables now
    appear in their true document position. The previous implementation read
    every paragraph first and every table afterwards, which relocated each
    table to the END of the document — a requirements table under
    "2. Requirements" was emitted after "5. Sign-off", severing it from its
    heading, and on any document over one chunk's worth of text every table
    landed together in the final chunk with the least surrounding context.

    No content is gained or lost by this change; only ordering is corrected.
    tests/test_doc_blocks.py asserts both halves of that claim
    (test_te001a_docx_render_preserves_all_content,
    test_te001b_docx_tables_appear_in_document_order).

    .txt/.md/.pdf still delegate to design_ingest.extract_text unchanged --
    that path returns the author's raw bytes, which preserves markdown
    heading markers and list bullets the LLM prompt benefits from seeing.
    Only .docx needs rendering, because .docx has no plain-text form.
    """
    ext = os.path.splitext(file_name.lower())[1]

    if ext in _DELEGATED_EXTENSIONS:
        # Unmodified reuse of the existing SOW-Checkpoints extractor's text
        # extraction -- identical need, zero duplicated logic/behavior.
        from app.services.design_ingest import extract_text

        return extract_text(storage_path, file_name)

    return doc_blocks.render_blocks(
        extract_existing_sow_blocks(storage_path, file_name)
    )
