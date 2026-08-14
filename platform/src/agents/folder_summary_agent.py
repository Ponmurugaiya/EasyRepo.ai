"""Folder Summary Agent.

Aggregates file-level summaries for all files within a folder into a single
folder summary with inline citations.

Two prompt modes controlled by ``intent``:
  "repository_overview"  → SHORT prompt  (3-5 sentences)
  "repository_detailed"  → DETAILED prompt (6-10 sentences)

Public API
----------
summarize_folder(folder, file_summaries, intent) -> tuple[str, int, int]
    Returns (summary_text, prompt_tokens, completion_tokens).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

SHORT_SYSTEM_PROMPT = """\
You are a code documentation assistant.
Given summaries of individual source files within a folder, write a SHORT folder summary.
The context header tells you exactly how many sentences to write — follow it precisely.

The summary should describe:
1. The folder's domain / responsibility (what it owns in the system)
2. The most important files and what they each provide
3. Key patterns across the folder (e.g. all files follow repository pattern, all expose REST routes)

Rules:
- Preserve inline citations exactly as they appear in the file summaries — copy them verbatim.
  Example citation format: [src/api/main.py:12-45]  ← integers only, never words like "start" or "end"
- Write exactly the number of sentences specified in the context header — no more, no less.
- Do NOT reproduce source code.
- Do NOT use markdown headers — write flowing prose.
"""

DETAILED_SYSTEM_PROMPT = """\
You are a code documentation assistant.
Given summaries of individual source files within a folder, write a DETAILED folder summary.
The context header tells you exactly how many sentences to write — follow it precisely.

The summary should describe:
1. The folder's domain / responsibility (what it owns in the system)
2. Every file and what it provides, with preserved inline citations
3. Cross-file interactions and data flow within the folder
4. Key design patterns and architectural decisions visible across the folder
5. External dependencies the folder introduces

Rules:
- Preserve inline citations exactly as they appear in the file summaries — copy them verbatim.
  Example citation format: [src/api/main.py:12-45]  ← integers only, never words like "start" or "end"
- Write exactly the number of sentences specified in the context header — no more, no less.
- Do NOT reproduce source code.
- Do NOT use markdown headers — write flowing prose.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_context(folder: str, file_summaries: dict[str, str], intent: str) -> str:
    """Build the context sent to the LLM."""
    n = len(file_summaries)

    if intent == "repository_detailed":
        # Detailed mode: more sentences, scales more aggressively with folder size
        if n <= 2:
            sentence_range = "6-7"
        elif n <= 5:
            sentence_range = "7-8"
        else:
            sentence_range = "8-10"
    else:
        # Short mode (overview): compact summaries
        if n <= 3:
            sentence_range = "3-4"
        elif n <= 6:
            sentence_range = "4-5"
        else:
            sentence_range = "5-6"

    parts = [f"Folder: {folder}  ({n} files — write {sentence_range} sentences)\n"]
    for file_path, summary in file_summaries.items():
        parts.append(f"--- {file_path} ---\n{summary}\n")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize_folder(
    folder: str,
    file_summaries: dict[str, str],
    intent: str = "repository_overview",
) -> "tuple[str, int, int]":
    """Aggregate file summaries into a folder-level summary.

    Parameters
    ----------
    folder:
        Folder path (e.g. "src/api", "src/storage", ".").
    file_summaries:
        dict mapping file_path → summary string for all files in this folder.
    intent:
        "repository_overview" → short prompt (3-5 sentences)
        "repository_detailed" → detailed prompt (6-10 sentences)

    Returns
    -------
    tuple[str, int, int]
        (summary_text, prompt_tokens, completion_tokens).
        Falls back to a minimal string and (0, 0) on LLM failure.
    """
    if not file_summaries:
        return f"`{folder}` — no files found.", 0, 0

    try:
        import src.generation.llm_client as _llm

        system_prompt = (
            DETAILED_SYSTEM_PROMPT
            if intent == "repository_detailed"
            else SHORT_SYSTEM_PROMPT
        )
        context = _build_context(folder, file_summaries, intent)
        query = f"Summarise the `{folder}` folder."

        # No force_model/force_provider — let the cascade run, but skip OpenRouter:
        # free tier throttles concurrent calls badly (80s+ per batch call).
        summary, _, prompt_tokens, completion_tokens = _llm.smart_complete(
            query=query,
            context=context,
            system_prompt=system_prompt,
            task_type="fast",
            skip_providers={"openrouter"},
        )
        return summary.strip(), prompt_tokens, completion_tokens

    except Exception as exc:
        logger.warning("folder_summary_agent: failed for %s: %s", folder, exc)
        file_names = ", ".join(f"`{fp}`" for fp in list(file_summaries.keys())[:5])
        return f"`{folder}` contains {len(file_summaries)} files: {file_names}.", 0, 0
