"""File Summary Agent.

Produces a 2-4 sentence summary of a single source file with inline citations.
Each summary includes the file's purpose, key classes/functions, and dependencies.

Citations use the exact ``[file_path:start_line-end_line]`` format recognised
by the citation validator so they link to real entities in the frontend.

Public API
----------
summarize_file(file_path, source, entities, repo_id) -> str
    Returns the summary string (with inline citations).
"""

from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.models import EntityModel

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a code documentation assistant.
Given source code from a single file and a list of its indexed entities, write a
2-4 sentence summary describing:
1. The file's purpose (what problem it solves or what it owns)
2. The key classes and functions defined in it, using inline citations
3. Its main external dependencies (imported modules/packages)

Citation format: [file_path:start_line-end_line] — use the EXACT file path provided.
Example: "The `authenticate` function [auth/service.py:45-89] validates JWT tokens."

Rules:
- Use the entity list to get exact line numbers — do NOT guess.
- Keep the summary to 2-4 sentences maximum.
- Do NOT reproduce source code blocks.
- Do NOT use markdown headers, only inline prose.
- Cite each named entity you mention using its exact line range.
"""


def _build_context(
    file_path: str,
    source: str,
    entities: "list[EntityModel]",
) -> str:
    """Build the context string sent to the LLM."""
    entity_lines = "\n".join(
        f"  - {e.type} `{e.name}` [{file_path}:{e.start_line}-{e.end_line}]"
        for e in entities
        if e.type in ("function", "class", "method", "interface")
    )
    # Cap source at ~2500 chars — increased from 1500 to capture more of larger
    # files (e.g. orchestrator.py, repo_overview.py) without exceeding fast-model
    # context limits. llama-3.1-8b-instant handles 131K tokens comfortably.
    source_preview = source[:2500] + ("..." if len(source) > 2500 else "")

    return (
        f"File: {file_path}\n\n"
        f"Indexed entities:\n{entity_lines or '  (none)'}\n\n"
        f"Source (first 2500 chars):\n```\n{source_preview}\n```"
    )


def summarize_file(
    file_path: str,
    source: str,
    entities: "list[EntityModel]",
    repo_id: str,
) -> str:
    """Summarise one source file with inline citations.

    Parameters
    ----------
    file_path:
        Canonical file path as stored in EntityModel.file_path.
    source:
        Full source text of the file.
    entities:
        EntityModel rows for this file (from the DB).
    repo_id:
        Repository ID (for logging).

    Returns
    -------
    tuple[str, int, int]
        (summary_text, prompt_tokens, completion_tokens).
        Falls back to a placeholder string and (0, 0) on any LLM failure.
    """
    try:
        import src.generation.llm_client as _llm

        context = _build_context(file_path, source, entities)
        query = f"Summarise the file `{file_path}`."

        # No force_model/force_provider — let the full cascade run.
        # Router prefers groq/llama-3.1-8b-instant first (fast tier, highest quota),
        # then falls through to gemini-2.5-flash-lite → nvidia_nim → cloudflare etc.
        # This prevents the concurrent-batch rate-limit burst seen when all 5 file
        # agents hammered a single model simultaneously.
        summary, _, prompt_tokens, completion_tokens = _llm.smart_complete(
            query=query,
            context=context,
            system_prompt=_SYSTEM_PROMPT,
            task_type="fast",
        )
        return summary.strip(), prompt_tokens, completion_tokens

    except Exception as exc:
        logger.warning("file_summary_agent: failed for %s: %s", file_path, exc)
        entity_names = ", ".join(
            e.name for e in entities[:5]
            if e.type in ("function", "class", "method")
        )
        fallback = (
            f"`{file_path}` — summary unavailable. "
            + (f"Contains: {entity_names}." if entity_names else "")
        )
        return fallback, 0, 0
