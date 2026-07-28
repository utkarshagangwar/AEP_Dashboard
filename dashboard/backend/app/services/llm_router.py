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
  VISUAL_LLM_PRIMARY    e.g. "axon/gemini-flash-latest"       (default)
  VISUAL_LLM_FALLBACKS  comma list, e.g. "gemini/gemini-2.5-flash-lite"
  VISUAL_LLM_TIMEOUT_S  per-call timeout, default 120
  VISUAL_LLM_MAX_RETRIES retries per model before falling back, default 2
  GEMINI_API_KEY / GOOGLE_API_KEY   Gemini key (litellm reads GEMINI_API_KEY)
  OPENROUTER_API_KEY                OpenRouter key
  AXON_API_KEY / AXON_BASE_URL      ATG metering gateway key (OpenAI-compatible
                                     proxy in front of Gemini Flash). It is the
                                     default chain's primary model; it can also
                                     be selected explicitly via
                                     model_override="axon/<model-id>", e.g.
                                     "axon/gemini-flash-latest". AXON_BASE_URL
                                     defaults to https://gw.atg.party/v1.

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

# AXON is the default for all router-backed AI work. Gemini remains the
# secondary/light provider when the gateway rejects a request or is out of
# budget. Override these only for an intentional deployment-level change.
_DEFAULT_PRIMARY = "axon/gemini-flash-latest"
_DEFAULT_FALLBACKS = "gemini/gemini-2.5-flash-lite,gemini/gemini-2.5-flash"
_DEFAULT_TIMEOUT_S = 120
_DEFAULT_MAX_RETRIES = 2
_BACKOFF_BASE_S = 2.0  # 2s, 4s, 8s ...

_AXON_DEFAULT_BASE_URL = "https://gw.atg.party/v1"


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
    # Provider's own finish_reason for the response `text` came from (the
    # repair call's, if a JSON repair pass ran -- see complete() below) --
    # "length" is the authoritative signal that the model was cut off by
    # max_tokens, not a heuristic guess from response size. None if the
    # provider/litellm didn't report one. Added for New Vibe Test Phase 5
    # (E.23): callers that ask for structured JSON (e.g. visual_judge.py's
    # vision pass) can detect and retry a truncated response instead of
    # silently persisting a findings array that stops mid-object.
    finish_reason: Optional[str] = field(default=None)


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

    if not (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("AXON_API_KEY", "").strip()
    ):
        raise LLMRouterError(
            "No Visual QA LLM key configured. Set GEMINI_API_KEY (or "
            "GOOGLE_API_KEY), OPENROUTER_API_KEY, and/or AXON_API_KEY in "
            "the environment."
        )


def _resolve_call_target(model: str) -> tuple[str, dict[str, str]]:
    """Translate a router chain entry into litellm's (model, extra_kwargs).

    "axon/<model-id>" routes through ATG's metering gateway — an
    OpenAI-compatible proxy in front of Gemini Flash, keyed by AXON_API_KEY
    rather than a native Gemini key. litellm has no built-in "axon"
    provider, so this maps it onto litellm's generic "openai/*" path with an
    explicit api_base/api_key override. Every other prefix (gemini/,
    openrouter/, anthropic/, ...) passes through unchanged and keeps using
    litellm's normal env-var-based key lookup.
    """
    if not model.startswith("axon/"):
        return model, {}

    real_model = model.split("/", 1)[1]
    api_key = os.environ.get("AXON_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("AXON_API_KEY is not set but an axon/* model was requested")
    base_url = os.environ.get("AXON_BASE_URL", "").strip() or _AXON_DEFAULT_BASE_URL
    return f"openai/{real_model}", {"api_base": base_url, "api_key": api_key}


def _usage_provider_and_key(model: str) -> tuple[str, str, Optional[str]]:
    """(provider, bare_model_id, key) for AI Usage logging — parsed from a
    router chain entry the same way _resolve_call_target reads it, kept
    separate so a logging-only change never touches the actual call-routing
    path above."""
    if model.startswith("axon/"):
        return "axon", model.split("/", 1)[1], os.environ.get("AXON_API_KEY", "").strip() or None
    if model.startswith("gemini/"):
        key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
        return "google", model.split("/", 1)[1], key or None
    if model.startswith("openrouter/"):
        return "openrouter", model.split("/", 1)[1], os.environ.get("OPENROUTER_API_KEY", "").strip() or None
    if model.startswith("anthropic/"):
        return "anthropic", model.split("/", 1)[1], os.environ.get("ANTHROPIC_API_KEY", "").strip() or None
    return "openai", model, os.environ.get("OPENAI_API_KEY", "").strip() or None


def _usage_source() -> str:
    """Best-effort caller identification for the AI Usage page, so every
    llm_router.complete() call site (design_ingest/orchestrator/sow_*/
    visual_judge) gets a meaningful "source" label without threading a new
    parameter through a dozen call sites. Falls back to "llm_router" if the
    call stack can't be inspected for any reason."""
    try:
        import inspect

        frame = inspect.stack()[2]  # [0]=this fn, [1]=complete(), [2]=actual caller
        module = inspect.getmodule(frame[0])
        name = (module.__name__ if module else "").rsplit(".", 1)[-1]
        return {
            "design_ingest": "brain",
            "orchestrator": "orchestrator",
            "visual_judge": "judge",
            "sow_ledger": "sow_ledger",
            "sow_assembly": "sow",
            "sow_audit": "sow",
            "sow_drafting": "sow",
        }.get(name, name or "llm_router")
    except Exception:
        return "llm_router"


def _log_usage(
    *,
    source: str,
    provider: str,
    model: str,
    key: Optional[str],
    status: str,
    response: Any = None,
    exc: Optional[BaseException] = None,
    duration_ms: Optional[int] = None,
    attempts: Optional[int] = None,
) -> None:
    """Thin adapter into app.services.ai_usage — kept local to this module
    (rather than called inline everywhere above) so the AI Usage page's
    logging concerns don't clutter complete()'s actual retry/fallback
    logic. Never raises; log_usage_event() is itself best-effort."""
    try:
        from app.services import ai_usage

        prompt_tokens = completion_tokens = total_tokens = None
        cost_usd = None
        if response is not None:
            usage = getattr(response, "usage", None)
            if usage is not None:
                prompt_tokens = getattr(usage, "prompt_tokens", None)
                completion_tokens = getattr(usage, "completion_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)
            try:
                import litellm

                cost_usd = litellm.completion_cost(completion_response=response)
            except Exception:
                cost_usd = ai_usage.estimate_cost_usd(provider, model, prompt_tokens, completion_tokens)

        http_status = None
        error_message = None
        if exc is not None:
            http_status = getattr(exc, "status_code", None)
            if not isinstance(http_status, int):
                http_status = None
            error_message = str(exc)

        ai_usage.log_usage_event(
            source=source,
            provider=provider,
            model=model,
            key=key,
            status=status,
            http_status=http_status,
            error_message=error_message,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost_usd,
            cost_is_approx=(provider == "axon"),
            duration_ms=duration_ms,
            attempts=attempts,
        )
    except Exception:
        pass


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
    usage_source = _usage_source()

    for model in chain:
        try:
            call_model, call_kwargs = _resolve_call_target(model)
        except RuntimeError as exc:
            # e.g. axon/* requested but AXON_API_KEY unset — skip straight
            # to the next model in the chain rather than aborting the call.
            errors.append(f"{model}: {exc}")
            logger.warning("LLM router: %s, falling back to next model", exc)
            continue

        usage_provider, usage_model, usage_key = _usage_provider_and_key(model)

        for attempt in range(1, max_retries + 2):  # initial try + retries
            attempts += 1
            try:
                response = litellm.completion(
                    model=call_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout_s,
                    **call_kwargs,
                )
                text = (response.choices[0].message.content or "").strip()
                if not text:
                    raise ValueError("Empty response from model")
                finish_reason = getattr(response.choices[0], "finish_reason", None)

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
                            model=call_model,
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
                            **call_kwargs,
                        )
                        repaired = (repair.choices[0].message.content or "").strip()
                        parsed = _extract_json(repaired)  # raises → next model
                        text = repaired
                        # The repair call is a fresh completion with its own
                        # finish_reason -- e.g. the original was cut off
                        # ("length") but the shorter repair prompt finished
                        # cleanly ("stop"), or vice versa. Report the one that
                        # actually produced the `text`/`parsed` being returned.
                        finish_reason = getattr(repair.choices[0], "finish_reason", None)

                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "LLM router: success model=%s attempts=%d duration_ms=%d",
                    model,
                    attempts,
                    duration_ms,
                )
                _log_usage(
                    source=usage_source, provider=usage_provider, model=usage_model,
                    key=usage_key, status="ok", response=response,
                    duration_ms=duration_ms, attempts=attempts,
                )
                return RouterResult(
                    text=text,
                    model_used=model,
                    attempts=attempts,
                    duration_ms=duration_ms,
                    parsed_json=parsed,
                    finish_reason=finish_reason,
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
                if attempt > max_retries:
                    # Retries exhausted for this model — log once here (not
                    # every retry, to avoid a burst of near-duplicate rows).
                    _log_usage(
                        source=usage_source, provider=usage_provider, model=usage_model,
                        key=usage_key, status="error", exc=exc, attempts=attempts,
                    )
                else:
                    time.sleep(wait_s)
                # else: exhausted retries for this model → fall through to next
            except Exception as exc:  # noqa: BLE001 — non-transient: skip to next model
                errors.append(f"{model}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "LLM router: non-transient error on %s, falling back: %s",
                    model,
                    exc,
                )
                _log_usage(
                    source=usage_source, provider=usage_provider, model=usage_model,
                    key=usage_key, status="error", exc=exc, attempts=attempts,
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
