"""LLM Router — single entry point for Visual QA LLM calls (Phase 1).

Implements "The Router" from the Visual QA architecture as an in-process
LiteLLM SDK wrapper (no proxy container). Provides:

  * A primary → fallback model chain, configured entirely via env vars so
    models can be swapped (e.g. when OpenRouter rotates its free models)
    without any code change.
  * Bounded retries with exponential backoff on transient/rate-limit errors.
  * Optional image inputs (base64 PNG) for vision calls used by the Judge.
  * Optional strict-JSON output mode with a single "repair" retry; on
    persistent invalid JSON the caller gets a clear failure, never
    fabricated content.

Env configuration (all optional except at least one provider key):
  VISUAL_LLM_PRIMARY    e.g. "gemini/gemini-3.5-flash"        (default)
  VISUAL_LLM_FALLBACKS  comma list, e.g. "gemini/gemini-2.5-flash,openrouter/qwen/qwen2.5-vl-72b-instruct:free"
  VISUAL_LLM_TIMEOUT_S  per-call timeout, default 120
  VISUAL_LLM_MAX_RETRIES retries per model before falling back, default 2
  GEMINI_API_KEY / GOOGLE_API_KEY   Gemini key (litellm reads GEMINI_API_KEY)
  OPENROUTER_API_KEY                OpenRouter key

This module is additive — nothing in the existing AI test runner
(ai_runner.py) imports or depends on it.
"""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

# flash-lite primary: free-tier quota is 1000 req/day/key vs 20/day for
# gemini-3.5-flash, which exhausted mid-run. Override via VISUAL_LLM_PRIMARY.
_DEFAULT_PRIMARY = "gemini/gemini-2.5-flash-lite"
_DEFAULT_FALLBACKS = "gemini/gemini-2.5-flash"
_DEFAULT_TIMEOUT_S = 120
_DEFAULT_MAX_RETRIES = 2
_BACKOFF_BASE_S = 2.0  # 2s, 4s, 8s ...


class LLMRouterError(RuntimeError):
    """Raised when every model in the chain failed for a call."""


@dataclass
class RouterResult:
    """Normalized response for router calls."""

    text: str
    model_used: str
    attempts: int
    duration_ms: int
    parsed_json: Optional[Any] = field(default=None)


def _model_chain() -> list[str]:
    """Build [primary, *fallbacks] from env, dropping blanks/duplicates."""
    primary = os.environ.get("VISUAL_LLM_PRIMARY", "").strip() or _DEFAULT_PRIMARY
    raw_fallbacks = os.environ.get("VISUAL_LLM_FALLBACKS", _DEFAULT_FALLBACKS)
    chain: list[str] = [primary]
    for name in raw_fallbacks.split(","):
        name = name.strip()
        if name and name not in chain:
            chain.append(name)
    return chain


def _validate_keys_present() -> None:
    """Fail fast with a clear message if no provider key is configured.

    litellm reads GEMINI_API_KEY for gemini/* models; many existing AEP
    deployments only set GOOGLE_API_KEY(S), so mirror it across before the
    first call rather than asking users to duplicate secrets.
    """
    if not os.environ.get("GEMINI_API_KEY"):
        google = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not google:
            # Support the plural rotation var used by ai_runner
            plural = os.environ.get("GOOGLE_API_KEYS", "")
            google = plural.split(",")[0].strip() if plural.strip() else ""
        if google:
            os.environ["GEMINI_API_KEY"] = google

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")):
        raise LLMRouterError(
            "No Visual QA LLM key configured. Set GEMINI_API_KEY (or "
            "GOOGLE_API_KEY) and/or OPENROUTER_API_KEY in the environment."
        )


def _build_messages(
    prompt: str,
    system: Optional[str],
    images_b64: Optional[list[str]],
) -> list[dict]:
    """Assemble OpenAI-format messages, embedding images as data URLs."""
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})

    if images_b64:
        content: list[dict] = [{"type": "text", "text": prompt}]
        for img in images_b64:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img}"},
                }
            )
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": prompt})
    return messages


def _extract_json(text: str) -> Any:
    """Parse a JSON object/array out of a model response.

    Tolerates markdown code fences, which several free models add even when
    told not to. Raises ValueError if nothing parseable is found.
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        # Strip ```json ... ``` fences
        candidate = candidate.split("```", 2)[1]
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Last resort: find outermost braces/brackets
        for open_ch, close_ch in (("{", "}"), ("[", "]")):
            start, end = candidate.find(open_ch), candidate.rfind(close_ch)
            if start != -1 and end > start:
                return json.loads(candidate[start : end + 1])
        raise ValueError("Model response contained no parseable JSON")


def complete(
    prompt: str,
    *,
    system: Optional[str] = None,
    images_b64: Optional[list[str]] = None,
    expect_json: bool = False,
    max_tokens: int = 4096,
    temperature: float = 0.0,
    model_override: Optional[str] = None,
) -> RouterResult:
    """Run a completion through the model chain with retries + fallback.

    Every model in the chain is tried in order; within a model, transient
    errors are retried with exponential backoff up to VISUAL_LLM_MAX_RETRIES.
    Non-transient errors (bad request, auth) skip straight to the next model.

    expect_json=True: response is parsed as JSON; one repair retry is made
    on the same model before treating it as a failure for that model.

    model_override: a single litellm model string (e.g. from
    app.services.model_pool.to_litellm_model_string()) that bypasses the
    deployment-static VISUAL_LLM_PRIMARY/VISUAL_LLM_FALLBACKS chain entirely
    — no cross-model fallback, since the caller (the orchestrator) already
    decided this specific model. None (the default) preserves the existing
    chain-based behavior for every other caller.

    Raises LLMRouterError if the entire chain is exhausted.
    """
    import litellm  # Lazy import: keep module importable without the dep installed
    from litellm import exceptions as llm_exc

    _validate_keys_present()

    timeout_s = int(os.environ.get("VISUAL_LLM_TIMEOUT_S", _DEFAULT_TIMEOUT_S))
    max_retries = int(os.environ.get("VISUAL_LLM_MAX_RETRIES", _DEFAULT_MAX_RETRIES))
    # Transient = worth retrying on the SAME model before falling back.
    transient_errors = (
        llm_exc.RateLimitError,
        llm_exc.Timeout,
        llm_exc.APIConnectionError,
        llm_exc.InternalServerError,
        llm_exc.ServiceUnavailableError,
    )

    messages = _build_messages(prompt, system, images_b64)
    chain = [model_override] if model_override else _model_chain()
    start = time.monotonic()
    attempts = 0
    errors: list[str] = []

    for model in chain:
        for attempt in range(1, max_retries + 2):  # initial try + retries
            attempts += 1
            try:
                response = litellm.completion(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout_s,
                )
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    raise ValueError("Empty response from model")

                parsed = None
                if expect_json:
                    try:
                        parsed = _extract_json(text)
                    except (ValueError, json.JSONDecodeError):
                        # One strict repair pass on the same model.
                        logger.warning(
                            "LLM router: invalid JSON from %s, attempting repair",
                            model,
                        )
                        repair = litellm.completion(
                            model=model,
                            messages=[
                                {
                                    "role": "user",
                                    "content": (
                                        "Convert the following into valid JSON only, "
                                        "no prose, no code fences:\n\n" + text
                                    ),
                                }
                            ],
                            max_tokens=max_tokens,
                            temperature=0.0,
                            timeout=timeout_s,
                        )
                        repaired = (repair.choices[0].message.content or "").strip()
                        parsed = _extract_json(repaired)  # raises → next model
                        text = repaired

                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "LLM router: success model=%s attempts=%d duration_ms=%d",
                    model,
                    attempts,
                    duration_ms,
                )
                return RouterResult(
                    text=text,
                    model_used=model,
                    attempts=attempts,
                    duration_ms=duration_ms,
                    parsed_json=parsed,
                )

            except transient_errors as exc:
                wait_s = _BACKOFF_BASE_S * (2 ** (attempt - 1))
                errors.append(f"{model}: {type(exc).__name__}")
                logger.warning(
                    "LLM router: transient error on %s (attempt %d/%d): %s",
                    model,
                    attempt,
                    max_retries + 1,
                    exc,
                )
                if attempt <= max_retries:
                    time.sleep(wait_s)
                # else: exhausted retries for this model → fall through to next
            except Exception as exc:  # noqa: BLE001 — non-transient: skip to next model
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "LLM router: non-transient error on %s, falling back: %s",
                    model,
                    exc,
                )
                break  # next model in chain

    duration_ms = int((time.monotonic() - start) * 1000)
    raise LLMRouterError(
        f"All models failed after {attempts} attempts in {duration_ms}ms: "
        + "; ".join(errors[-len(chain) * 2 :])
    )


def encode_image_file(path: str) -> str:
    """Read an image file and return base64 for use with complete(images_b64=...)."""
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")
