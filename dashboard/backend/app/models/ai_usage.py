"""AI Usage tracking — every LLM API call made by the platform, plus
admin-set manual quota overrides per key.

Tables:
  ai_usage_events — one row per LLM call attempt (success or failure),
                    across every subsystem: Hands (ai_runner.py, the
                    browser-use agent — login-sequence/Vibe tests), Judge
                    and Brain/SOW ingestion (llm_router.py), and the
                    orchestrator's own classifier calls. Deliberately a
                    flat, source-agnostic event log — same shape as
                    AuditLog (String source/resource fields, not a hard FK
                    per possible parent table) rather than one row shape
                    per subsystem, so a single dashboard/query covers all
                    of them without N join paths.
  ai_key_limits   — admin-set manual quota override per key_label, for
                    providers/keys where no reliable auto-detected limit
                    exists (Anthropic/OpenAI/OpenRouter are pay-as-you-go
                    with no fixed cap; Google's actual free-tier RPD is not
                    a published guaranteed number per Google's own rate
                    limits docs, so it's manual-override-only too unless
                    the admin wants a numeric progress bar). AXON's $10
                    metered budget (see AXON_API_KEY comment in .env) is
                    the one provider with a genuinely known default, and
                    that default lives in app.services.ai_usage as a
                    constant/env var, not here — this table only holds
                    overrides someone actually typed in.

Both additive only — no existing table or model is modified.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AIUsageEvent(Base):
    """One LLM call attempt. Written best-effort by
    app.services.ai_usage.log_usage_event() — a failure to write this row
    must never break the actual LLM call it's describing (see that
    module's docstring)."""

    __tablename__ = "ai_usage_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # Which subsystem made the call — free-text like AuditLog.action, not
    # an enum, so a new call site doesn't need a migration to be logged.
    # Known values today: "hands", "judge", "brain", "sow_ledger",
    # "video_ingest", "orchestrator_classifier", "quota_probe" (the last is
    # the cheap 1-token liveness check ai_runner.py already made before
    # this feature existed — logged for Google quota-status display, and
    # excluded from the main usage totals; see ai_usage.py).
    source: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(200), nullable=False)

    # Masked identifier, e.g. "google:...zGzQg8" — last 6 chars of the real
    # key only. NEVER the raw secret. See ai_usage.mask_key_label().
    key_label: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False)  # "ok" | "error"
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(12, 6), nullable=True)

    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    attempts: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Loose link back to whatever run/document/audit triggered this call —
    # same resource_type/resource_id convention as AuditLog, deliberately
    # not an FK (the caller can be an AITestRun, VisualRun, OrchestratorRun,
    # SowDocument, ... no single FK target fits all of them).
    run_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    run_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    extra: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<AIUsageEvent id={self.id} source={self.source} provider={self.provider} "
            f"model={self.model} status={self.status}>"
        )


class AIKeyLimit(Base):
    """Admin-set manual quota override for one key_label. Optional — a key
    with no row here just shows as "no fixed limit" (or AXON's auto
    default) in the usage UI."""

    __tablename__ = "ai_key_limits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key_label: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)

    # "requests_per_day" | "budget_usd" — see ai_usage.py's REMAINING calc.
    limit_type: Mapped[str] = mapped_column(String(20), nullable=False)
    limit_value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)

    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<AIKeyLimit key_label={self.key_label} {self.limit_type}={self.limit_value}>"
