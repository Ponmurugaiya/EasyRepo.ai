"""Generation package — multi-provider answer generation with citation validation.

Provider cascade: Groq (6-model rotation) → Gemini fallback, via LiteLLM.
All LLM calls go through a single module (``llm_client.py``); there are no
longer separate per-provider SDK files.

Public API
----------
generate_answer_with_fallback(...) -> tuple[str, str]
    Recommended entry point.  Returns ``(answer, provider_used)``.

generate_answer(...)  -> str
    Legacy Gemini-only shim — kept so existing code doesn't break.

LLMProviderError
    Raised when every configured provider fails.

GeminiClientError, GroqClientError
    Backwards-compatible aliases for ``LLMProviderError``.

validate_citations(answer, context_entities, ...) -> ValidationReport
    3-way citation classifier (definition / call-site / unsupported).

build_system_prompt() -> str
    System prompt that constrains citation format and evidence priority.

render_context_for_prompt(final_context) -> str
    Renders a FinalContext into a structured user-turn string.
"""

from src.generation.llm_client import (
    GROQ_MODELS,
    GROQ_MODEL_NAMES,
    GeminiClientError,
    GroqClientError,
    LLMProviderError,
    generate_answer,
    generate_answer_with_fallback,
)
from src.generation.prompt_templates import build_system_prompt, render_context_for_prompt
from src.generation.citation_validator import (
    CitationMatch,
    CitationMismatch,
    ValidationReport,
    validate_citations,
)

__all__ = [
    # Multi-provider router (recommended)
    "generate_answer_with_fallback",
    "LLMProviderError",
    # Legacy shim
    "generate_answer",
    # Backwards-compat exception aliases
    "GeminiClientError",
    "GroqClientError",
    # Model catalogue
    "GROQ_MODELS",
    "GROQ_MODEL_NAMES",
    # Prompts
    "build_system_prompt",
    "render_context_for_prompt",
    # Citation validation
    "CitationMatch",
    "CitationMismatch",
    "ValidationReport",
    "validate_citations",
]
