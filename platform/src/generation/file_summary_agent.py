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
    # Cap source at ~1500 chars to stay within fast-model context
    source_preview = source[:1500] + ("..." if len(source) > 1500 else "")

    return (
        f"File: {file_path}\n\n"
        f"Indexed entities:\n{entity_lines or '  (none)'}\n\n"
        f"Source (first 1500 chars):\n```\n{source_preview}\n```"
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
    str
        2-4 sentence summary with inline [file:line-line] citations.
        Returns a minimal fallback string on any LLM failure.
    """
    try:
        import src.generation.llm_client as _llm

        context = _build_context(file_path, source, entities)
        query = f"Summarise the file `{file_path}`."

        summary, _ = _llm.smart_complete(
            query=query,
            context=context,
            system_prompt=_SYSTEM_PROMPT,
            task_type="fast",
            force_model="groq/llama-3.1-8b-instant",
        )
        return summary.strip()

    except Exception as exc:
        # Log but don't crash — return a minimal placeholder so the overview
        # pipeline can continue with other files.
        logger.warning("file_summary_agent: failed for %s: %s", file_path, exc)
        entity_names = ", ".join(
            e.name for e in entities[:5]
            if e.type in ("function", "class", "method")
        )
        return (
            f"`{file_path}` — summary unavailable. "
            + (f"Contains: {entity_names}." if entity_names else "")
        )
