"""Generation package — Gemini-powered answer generation with citation validation.

Public API
----------
generate_answer(query, context, model) -> str
    Call Gemini 2.5 Flash with structured context and return the generated answer.

validate_citations(answer, context_entities) -> ValidationReport
    Parse every [file_path:start-end] citation in *answer* and verify each one
    against the actual entities in *context_entities*.

build_system_prompt() -> str
    Return the system prompt that constrains citation behaviour and trace ordering.

render_context_for_prompt(final_context) -> str
    Render a FinalContext into a structured user-turn string that preserves the
    [CORE] / [PARENT] / [CALL CHAIN] / [CALLERS] / [INHERITANCE] hierarchy.
"""

from src.generation.gemini_client import GeminiClientError, generate_answer
from src.generation.prompt_templates import build_system_prompt, render_context_for_prompt
from src.generation.citation_validator import (
    CitationMatch,
    CitationMismatch,
    ValidationReport,
    validate_citations,
)

__all__ = [
    "generate_answer",
    "GeminiClientError",
    "build_system_prompt",
    "render_context_for_prompt",
    "CitationMatch",
    "CitationMismatch",
    "ValidationReport",
    "validate_citations",
]
