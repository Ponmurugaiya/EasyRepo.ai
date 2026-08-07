"""Generation package — multi-provider answer generation with citation validation.

Smart proactive routing: selects the best model for the task BEFORE calling it,
based on context size, task complexity, and live quota state. Provider cascade
(used only when a model is quota-exhausted): Groq → Gemini → OpenRouter → Cohere → Cloudflare.

Public API
----------
smart_complete(query, context, system_prompt, task_type, ...) -> tuple[str, str]
    Primary entry point. Proactively routes to the best model for the task.

generate_answer_with_fallback(...) -> tuple[str, str]
    Backwards-compatible wrapper over smart_complete.

generate_answer(...) -> str
    Legacy Gemini-only shim.

LLMProviderError
    Raised when every configured model is unavailable or quota-exhausted.

ModelSpec
    Dataclass describing one model's capabilities and quota limits.

ALL_MODEL_SPECS : list[ModelSpec]
    Full catalogue of all configured models with routing metadata.
"""

from src.generation.llm_client import (
    # Primary routing API
    smart_complete,
    ModelSpec,
    ALL_MODEL_SPECS,
    # Backwards-compat
    generate_answer_with_fallback,
    generate_answer,
    LLMProviderError,
    GeminiClientError,
    GroqClientError,
    # Model lists (name-only, for CLI/API matching)
    GROQ_MODELS,
    GROQ_MODEL_NAMES,
    GEMINI_MODELS,
    CEREBRAS_MODELS,
    OPENROUTER_FREE_MODELS,
    COHERE_FREE_MODELS,
    CLOUDFLARE_FREE_MODELS,
    ALL_FREE_MODELS,
)
from src.generation.prompt_templates import build_system_prompt, render_context_for_prompt
from src.generation.citation_validator import (
    CitationMatch,
    CitationMismatch,
    ValidationReport,
    validate_citations,
)

__all__ = [
    # Primary
    "smart_complete",
    "ModelSpec",
    "ALL_MODEL_SPECS",
    # Backwards-compat
    "generate_answer_with_fallback",
    "generate_answer",
    "LLMProviderError",
    "GeminiClientError",
    "GroqClientError",
    # Model lists
    "GROQ_MODELS",
    "GROQ_MODEL_NAMES",
    "GEMINI_MODELS",
    "CEREBRAS_MODELS",
    "OPENROUTER_FREE_MODELS",
    "COHERE_FREE_MODELS",
    "CLOUDFLARE_FREE_MODELS",
    "ALL_FREE_MODELS",
    # Prompts
    "build_system_prompt",
    "render_context_for_prompt",
    # Citation validation
    "CitationMatch",
    "CitationMismatch",
    "ValidationReport",
    "validate_citations",
]
