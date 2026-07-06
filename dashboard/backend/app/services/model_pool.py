"""Model pool — single source of truth for which LLM providers are actually
usable given the configured env vars, plus the factory that turns an
abstract model choice into either a LangChain client (for Hands, via
ai_runner's provider helpers) or a litellm model string (for Judge /
self-execution, via llm_router.complete()).

This module only serves the orchestrator (app/services/orchestrator.py). The
existing static defaults elsewhere are untouched: ai_runner._build_llm()
still applies its own Anthropic->OpenAI->Google precedence when no
llm_override is given, and llm_router.complete() still falls back to its own
VISUAL_LLM_PRIMARY/VISUAL_LLM_FALLBACKS chain when no model_override is given.

Kept as its own leaf module (rather than folded into orchestrator.py) so
ai_runner.py never has to import orchestrator.py — this module imports
FROM ai_runner (for the three provider-client helpers), never the reverse.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_ANTHROPIC_MODEL = "claude-3-5-haiku-20241022"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"


@dataclass
class ModelChoice:
    """An abstract model selection the orchestrator can act on."""

    provider: str  # "anthropic" | "openai" | "google" | "openrouter"
    model: str  # provider-native model id, e.g. "google/gemma-4-26b-a4b-it:free"
    tier: str  # "cheap" | "capable" — used by the coordinator's judgment call
    reason: str  # human-readable, stored in the audit trail (OrchestratorStepDecision.rationale)


def _get_key_list(env_name_plural: str, env_name_singular: str) -> list[str]:
    """Same convention as ai_runner._get_key_list — duplicated (not imported)
    to keep this module import-independent of ai_runner for the pool-scan
    functions below; only to_langchain_client() imports ai_runner, lazily."""
    raw = os.environ.get(env_name_plural, "")
    if raw.strip():
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if keys:
            return keys
    single = os.environ.get(env_name_singular, "").strip()
    return [single] if single else []


def available_pool() -> list[ModelChoice]:
    """Inspect env vars and return only entries whose key is both present
    AND actually usable right now (live-validated, not just "the env var is
    set"). Never raises — an empty pool means the caller must fall back to
    a deterministic-only / no-LLM path.

    A provider whose key exists but is rejected (invalid/expired) or, for
    Google, whose daily free-tier quota is exhausted for every candidate
    model, is left out of the pool entirely — reusing ai_runner's own
    live-probe helpers so this never drifts out of sync with what
    _build_llm() would actually be able to use (see resolve_google_provider
    in ai_runner.py for why this matters)."""
    from app.services import ai_runner

    pool: list[ModelChoice] = []
    model_override = os.environ.get("AI_LLM_MODEL", "").strip()

    anthropic_keys = _get_key_list("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")
    if anthropic_keys and ai_runner._anthropic_key_valid(anthropic_keys[0]):
        pool.append(
            ModelChoice(
                "anthropic",
                model_override or _DEFAULT_ANTHROPIC_MODEL,
                "capable",
                "Anthropic API key configured",
            )
        )

    openai_keys = _get_key_list("OPENAI_API_KEYS", "OPENAI_API_KEY")
    if openai_keys and ai_runner._openai_key_valid(openai_keys[0]):
        pool.append(
            ModelChoice(
                "openai",
                model_override or _DEFAULT_OPENAI_MODEL,
                "capable",
                "OpenAI API key configured",
            )
        )

    google_resolved = ai_runner.resolve_google_provider(model_override or None)
    if google_resolved:
        google_model, _keys = google_resolved
        pool.append(
            ModelChoice(
                "google",
                google_model,
                "capable",
                "Google API key configured",
            )
        )

    if os.environ.get("OPENROUTER_API_KEY", "").strip():
        pool.append(
            ModelChoice(
                "openrouter",
                os.environ.get("ORCHESTRATOR_CHEAP_MODEL", "").strip()
                or _DEFAULT_OPENROUTER_MODEL,
                "cheap",
                "OpenRouter API key configured (free tier)",
            )
        )

    return pool


def cheapest(pool: list[ModelChoice]) -> Optional[ModelChoice]:
    """First tier=='cheap' entry, else first entry at all, else None."""
    for choice in pool:
        if choice.tier == "cheap":
            return choice
    return pool[0] if pool else None


def most_capable(pool: list[ModelChoice]) -> Optional[ModelChoice]:
    """First tier=='capable' entry, else fall back to cheapest."""
    for choice in pool:
        if choice.tier == "capable":
            return choice
    return cheapest(pool)


def to_langchain_client(choice: ModelChoice):
    """Build a LangChain BaseChatModel for browser-use's Agent(llm=...).

    Raises ValueError for an unknown provider or a missing key — callers
    (orchestrator.execute_run) must catch this and fall back to the
    default _build_llm() precedence rather than fail the whole run.
    """
    from app.services import ai_runner

    if choice.provider == "anthropic":
        keys = _get_key_list("ANTHROPIC_API_KEYS", "ANTHROPIC_API_KEY")
        if not keys or not ai_runner._anthropic_key_valid(keys[0]):
            raise ValueError("No usable Anthropic API key available right now")
        return ai_runner._anthropic_client(choice.model, keys)

    if choice.provider == "openai":
        keys = _get_key_list("OPENAI_API_KEYS", "OPENAI_API_KEY")
        if not keys or not ai_runner._openai_key_valid(keys[0]):
            raise ValueError("No usable OpenAI API key available right now")
        return ai_runner._openai_client(choice.model, keys)

    if choice.provider == "google":
        # No re-probe here: available_pool() already live-validated this
        # choice moments earlier via resolve_google_provider(), and
        # _google_client() below already rotates across all configured
        # keys at runtime the instant one hits a 429 (see ai_runner.py's
        # _RotatingGoogleChat) — a second up-front probe would just be a
        # redundant API call racing the same tiny daily quota, and a
        # transient failure there would wrongly discard an otherwise-good
        # choice (confirmed: this is exactly what happened in testing).
        keys = _get_key_list("GOOGLE_API_KEYS", "GOOGLE_API_KEY")
        if not keys:
            raise ValueError("No Google API key configured")
        return ai_runner._google_client(choice.model, keys)

    if choice.provider == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            raise ValueError("No OpenRouter API key configured")
        from langchain_openai import ChatOpenAI

        # OpenRouter is OpenAI-API-compatible — this is the exact mechanism
        # verified working with a live call to google/gemma-4-26b-a4b-it:free.
        logger.info("Model pool: building OpenRouter client for Hands, model=%s", choice.model)
        return ChatOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            model=choice.model,
        )

    raise ValueError(f"Unknown model provider: {choice.provider}")


def to_litellm_model_string(choice: ModelChoice) -> str:
    """Convert to the model string llm_router.complete(model_override=...) expects."""
    if choice.provider == "openrouter":
        return f"openrouter/{choice.model}"
    if choice.provider == "google":
        return f"gemini/{choice.model}"
    if choice.provider == "anthropic":
        return f"anthropic/{choice.model}"
    if choice.provider == "openai":
        return choice.model
    raise ValueError(f"Unknown model provider: {choice.provider}")
