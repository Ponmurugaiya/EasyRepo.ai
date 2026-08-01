"""Unified LLM client backed by LiteLLM.

LiteLLM speaks to every provider (Groq, Gemini, OpenAI, Anthropic, …) through
one ``completion()`` call, handles retries and rate-limit back-off internally,
and normalises the response object so we never write provider-specific SDK code
again.

Provider cascade (default)
--------------------------
1. Groq — tried with 6 models in order; each model has its own free-tier quota
   bucket so rotating multiplies effective capacity.
2. Gemini — fallback when every Groq model is exhausted or the key is absent.

Callers always get back ``(answer: str, provider_used: str)``.

Public API
----------
generate_answer_with_fallback(...) -> tuple[str, str]
    Recommended entry point for all answer generation.

generate_answer(...)  -> str
    Legacy Gemini-only shim — kept so existing imports don't break.

GROQ_MODELS : list[str]
    Ordered list of Groq model IDs tried in sequence.

LLMProviderError
    Raised when every configured provider fails.

GeminiClientError, GroqClientError
    Thin aliases of LLMProviderError kept for backwards-compatible imports.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model catalogue — verified against live Groq API + LiteLLM 2026-08-01
# ---------------------------------------------------------------------------
#
# Verification methodology:
#   1. Listed all active text-to-text models from /v1/models
#   2. Tested each with a realistic code-QA prompt via direct Groq API
#   3. Tested correct LiteLLM model string format (groq/ prefix rules)
#
# EXCLUDED models and reasons:
#   meta-llama/llama-prompt-guard-2-{22m,86m} — content classifiers, HTTP 400
#   qwen/qwen3.6-27b                          — emits <think>…</think> traces
#                                               that corrupt citation regex parsing
#   groq/groq/compound (full)                 — adds unsolicited agentic commentary
#   groq/openai/gpt-oss-{20b,120b,safeguard}  — return empty content via LiteLLM
#
# LiteLLM prefix rule for Groq models:
#   - Bare IDs (llama-3.3-70b-versatile)  → "groq/<id>"
#   - IDs with a slash (groq/compound-mini, openai/gpt-oss-*) → "groq/<id>"
#     i.e. groq/compound-mini  → "groq/groq/compound-mini"  ← correct
#          openai/gpt-oss-120b → "groq/openai/gpt-oss-120b" ← resolves but empty
#
# Ordered best-quality-first so the first success is the best answer.
# Each model has its own free-tier RPM/TPM quota bucket.
GROQ_MODELS: list[str] = [
    "groq/llama-3.3-70b-versatile",   # Meta Llama 3.3 70B   — best quality, 128k ctx
    "groq/groq/compound-mini",        # Groq Compound Mini   — fast composite, 128k ctx
    "groq/llama-3.1-8b-instant",      # Meta Llama 3.1 8B    — highest throughput quota
    "groq/allam-2-7b",                # SDAIA Allam 2 7B     — separate provider bucket
]

# Bare model names (without the "groq/" prefix) for CLI/API model matching
GROQ_MODEL_NAMES: list[str] = [m.split("/", 1)[1] for m in GROQ_MODELS]

_DEFAULT_GEMINI_MODEL = "gemini/gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class LLMProviderError(RuntimeError):
    """Raised when every configured LLM provider fails."""


# Backwards-compatible aliases so existing imports keep working
GeminiClientError = LLMProviderError
GroqClientError = LLMProviderError


# ---------------------------------------------------------------------------
# Lazy LiteLLM import
# ---------------------------------------------------------------------------

def _import_litellm():
    try:
        import litellm
        return litellm
    except ImportError as exc:
        raise ImportError(
            "The 'litellm' package is required for answer generation.\n"
            "Install it with:  pip install litellm"
        ) from exc


# ---------------------------------------------------------------------------
# Core generation — single provider attempt
# ---------------------------------------------------------------------------

def _call_model(
    litellm,
    model: str,
    system_prompt: str,
    context: str,
    api_key: Optional[str] = None,
) -> str:
    """Make one LiteLLM completion call and return the answer text."""
    kwargs: dict = dict(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": context},
        ],
        temperature=0.2,
        max_tokens=8192,
    )
    if api_key:
        kwargs["api_key"] = api_key

    response = litellm.completion(**kwargs)
    answer = response.choices[0].message.content
    if not answer:
        raise LLMProviderError(f"Model {model!r} returned an empty response.")
    return answer


# ---------------------------------------------------------------------------
# Public: multi-provider generation with Groq → Gemini fallback
# ---------------------------------------------------------------------------

def generate_answer_with_fallback(
    query: str,
    context: str,
    system_prompt: str,
    # Groq options
    groq_model: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    # Gemini options
    gemini_model: str = "gemini-2.5-flash",
    gemini_api_key: Optional[str] = None,
    # Routing overrides
    skip_groq: bool = False,
    skip_gemini: bool = False,
) -> tuple[str, str]:
    """Try Groq first, fall back to Gemini on any failure.

    Parameters
    ----------
    query:
        The original natural-language question (used for logging only).
    context:
        Structured user-turn content from ``render_context_for_prompt``.
    system_prompt:
        System instruction from ``build_system_prompt()``.
    groq_model:
        A specific Groq model name (with or without ``groq/`` prefix), or
        ``None`` to rotate through :data:`GROQ_MODELS` automatically.
    groq_api_key:
        Groq API key override; falls back to ``GROQ_API_KEY`` env var.
    gemini_model:
        Gemini model name (with or without ``gemini/`` prefix).
        Default: ``"gemini-2.5-flash"``.
    gemini_api_key:
        Gemini API key override; falls back to ``GEMINI_API_KEY`` env var.
    skip_groq:
        Force-skip Groq (e.g. caller explicitly wants Gemini only).
    skip_gemini:
        Force-skip Gemini fallback.

    Returns
    -------
    tuple[str, str]
        ``(answer_text, provider_used)`` — provider is ``"groq"`` or
        ``"gemini"``.

    Raises
    ------
    LLMProviderError
        If every enabled provider fails.
    """
    litellm = _import_litellm()

    # Silence LiteLLM's verbose success logging; we do our own.
    litellm.success_callback = []
    litellm.failure_callback = []

    resolved_groq_key = groq_api_key or os.environ.get("GROQ_API_KEY", "")
    resolved_gemini_key = gemini_api_key or os.environ.get("GEMINI_API_KEY", "")

    if not resolved_groq_key:
        skip_groq = True
    if not resolved_gemini_key:
        skip_gemini = True

    errors: list[str] = []

    # ── Groq (primary) ──────────────────────────────────────────────────────
    if not skip_groq:
        # Build list of litellm model strings to try
        if groq_model:
            # Normalise: accept "llama3-70b-8192" or "groq/llama3-70b-8192"
            groq_litellm = (
                groq_model if groq_model.startswith("groq/")
                else f"groq/{groq_model}"
            )
            models_to_try = [groq_litellm]
        else:
            models_to_try = list(GROQ_MODELS)

        for model_id in models_to_try:
            try:
                logger.info("LLM: trying %s", model_id)
                answer = _call_model(
                    litellm, model_id, system_prompt, context,
                    api_key=resolved_groq_key,
                )
                logger.info("LLM: %s succeeded (%d chars)", model_id, len(answer))
                return answer, "groq"

            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                errors.append(f"{model_id}: {type(exc).__name__}: {exc}")

                # Daily/per-day quota exhausted → skip to next model immediately
                if any(k in msg for k in ("per day", "daily", "tokens per day")):
                    logger.warning("LLM: %s daily quota exhausted, trying next.", model_id)
                    continue

                # Transient rate-limit → LiteLLM already retried internally,
                # so log and move on to the next model
                logger.warning("LLM: %s failed (%s), trying next model.", model_id, type(exc).__name__)
                continue

        logger.warning("LLM: all Groq models exhausted, falling back to Gemini.")

    # ── Gemini (fallback) ────────────────────────────────────────────────────
    if not skip_gemini:
        # Normalise: accept "gemini-2.5-flash" or "gemini/gemini-2.5-flash"
        gemini_litellm = (
            gemini_model if gemini_model.startswith("gemini/")
            else f"gemini/{gemini_model}"
        )
        try:
            logger.info("LLM: trying %s", gemini_litellm)
            answer = _call_model(
                litellm, gemini_litellm, system_prompt, context,
                api_key=resolved_gemini_key,
            )
            logger.info("LLM: %s succeeded (%d chars)", gemini_litellm, len(answer))
            return answer, "gemini"

        except Exception as exc:  # noqa: BLE001
            errors.append(f"{gemini_litellm}: {type(exc).__name__}: {exc}")
            logger.error("LLM: Gemini also failed: %s", exc)

    # ── All providers exhausted ──────────────────────────────────────────────
    if not errors:
        raise LLMProviderError(
            "No LLM providers are configured. "
            "Set GROQ_API_KEY and/or GEMINI_API_KEY environment variables."
        )
    raise LLMProviderError(
        "All LLM providers failed.\n" + "\n".join(f"  {e}" for e in errors)
    )


# ---------------------------------------------------------------------------
# Legacy shim — generate_answer (Gemini-only, kept for backwards compat)
# ---------------------------------------------------------------------------

def generate_answer(
    query: str,
    context: str,
    system_prompt: str,
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
) -> str:
    """Gemini-only generation shim.

    Kept so any code that still imports ``generate_answer`` from the old
    ``gemini_client`` path continues to work.  New code should use
    :func:`generate_answer_with_fallback` instead.
    """
    answer, _ = generate_answer_with_fallback(
        query=query,
        context=context,
        system_prompt=system_prompt,
        gemini_model=model,
        gemini_api_key=api_key,
        skip_groq=True,     # shim is Gemini-only by design
        skip_gemini=False,
    )
    return answer
