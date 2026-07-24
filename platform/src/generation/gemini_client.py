"""Gemini 2.5 Flash client with retry logic.

Uses the current ``google-genai`` SDK (not the deprecated ``google-generativeai``).
API key is read exclusively from the ``GEMINI_API_KEY`` environment variable.

Public API
----------
generate_answer(query, context, model) -> str
    Single-shot generation with exponential backoff on rate-limit / timeout errors.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class GeminiClientError(RuntimeError):
    """Raised when all retries to the Gemini API are exhausted."""


# ---------------------------------------------------------------------------
# Lazy import — guard against missing package with a clear message
# ---------------------------------------------------------------------------

def _import_genai():
    """Lazy-import google.genai and return the module, with a friendly error."""
    try:
        import google.genai as genai  # google-genai package
        return genai
    except ImportError as exc:
        raise ImportError(
            "The 'google-genai' package is required for answer generation.\n"
            "Install it with:  pip install google-genai"
        ) from exc


# ---------------------------------------------------------------------------
# Retry helpers
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 503}  # rate-limit, service unavailable

_MAX_RETRIES = 3
_BASE_BACKOFF_S = 2.0   # seconds; doubles each attempt


def _is_retryable(exc: Exception) -> bool:
    """Return True if the exception warrants a retry."""
    exc_type = type(exc).__name__
    # google-genai raises google.api_core.exceptions for HTTP errors
    retryable_names = {
        "ResourceExhausted",
        "ServiceUnavailable",
        "DeadlineExceeded",
        "TooManyRequests",
        "Aborted",
    }
    if exc_type in retryable_names:
        return True
    # Also catch timeout variants by message
    msg = str(exc).lower()
    if "timeout" in msg or "rate limit" in msg or "quota" in msg:
        return True
    return False


# ---------------------------------------------------------------------------
# Core generation function
# ---------------------------------------------------------------------------

def generate_answer(
    query: str,
    context: str,
    system_prompt: str,
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
) -> str:
    """Generate an answer from Gemini 2.5 Flash grounded in *context*.

    Parameters
    ----------
    query:
        The original natural-language question (used for logging only — it is
        already embedded in *context* by ``render_context_for_prompt``).
    context:
        Structured user-turn content produced by ``render_context_for_prompt``.
    system_prompt:
        The system instruction string from ``build_system_prompt()``.
    model:
        Gemini model identifier (default: ``"gemini-2.5-flash"``).
    api_key:
        Optional override for the API key.  If ``None``, reads
        ``GEMINI_API_KEY`` from the environment.

    Returns
    -------
    str
        The model's text response.

    Raises
    ------
    GeminiClientError
        If all ``_MAX_RETRIES`` attempts fail.
    ValueError
        If no API key is available.
    """
    resolved_key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not resolved_key:
        raise ValueError(
            "No Gemini API key found. Set the GEMINI_API_KEY environment "
            "variable or pass --gemini-key on the command line."
        )

    genai = _import_genai()

    # The google-genai SDK prefers GOOGLE_API_KEY over GEMINI_API_KEY when
    # both are present in the environment, even when api_key= is passed
    # explicitly.  Temporarily remove it so our resolved_key is authoritative.
    _stashed_google_key = os.environ.pop("GOOGLE_API_KEY", None)
    try:
        client = genai.Client(api_key=resolved_key)
    finally:
        if _stashed_google_key is not None:
            os.environ["GOOGLE_API_KEY"] = _stashed_google_key

    last_exc: Optional[Exception] = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            logger.debug("Gemini request attempt %d/%d (model=%s)", attempt, _MAX_RETRIES, model)
            response = client.models.generate_content(
                model=model,
                contents=context,
                config=genai.types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.2,   # low temperature for factual/grounded answers
                    max_output_tokens=4096,
                ),
            )
            # Extract text from the first candidate
            answer = response.text
            if not answer:
                raise GeminiClientError("Gemini returned an empty response.")
            logger.debug("Gemini response received (%d chars)", len(answer))
            return answer

        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if _is_retryable(exc) and attempt < _MAX_RETRIES:
                backoff = _BASE_BACKOFF_S * (2 ** (attempt - 1))
                logger.warning(
                    "Gemini API error on attempt %d/%d (%s). Retrying in %.1fs…",
                    attempt,
                    _MAX_RETRIES,
                    type(exc).__name__,
                    backoff,
                )
                time.sleep(backoff)
            else:
                break

    raise GeminiClientError(
        f"Gemini API call failed after {_MAX_RETRIES} attempts. "
        f"Last error: {last_exc}"
    ) from last_exc
