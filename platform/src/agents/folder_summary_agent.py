"""Folder Summary Agent.

Aggregates file-level summaries for all files within a folder into a single
3-5 sentence folder summary with inline citations.

Public API
----------
summarize_folder(folder, file_summaries) -> str
    Returns the folder summary string.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a code documentation assistant.
Given summaries of individual source files within a folder, write a folder summary.
The context header tells you exactly how many sentences to write — follow it precisely.

The summary should describe:
1. The folder's domain / responsibility (what it owns in the system)
2. The most important files and what they each provide
3. Key patterns across the folder (e.g. all files follow repository pattern, all expose REST routes, etc.)
4. Cross-file dependencies visible from the summaries

Rules:
- Preserve inline citations ([file_path:start-end]) from the file summaries when you mention specific entities.
- Write exactly the number of sentences specified in the context header — no more, no less.
- Do NOT reproduce source code.
- Do NOT use markdown headers — write flowing prose.
"""


def _build_context(folder: str, file_summaries: dict[str, str]) -> str:
    """Build the context sent to the LLM."""
    # Scale the requested sentence count with folder size so larger folders
    # get proportionally richer summaries (3 sentences for 1-3 files, up to 8
    # for folders with 10+ files).
    n = len(file_summaries)
    if n <= 3:
        sentence_range = "3-4"
    elif n <= 6:
        sentence_range = "4-6"
    else:
        sentence_range = "6-8"

    parts = [f"Folder: {folder}  ({n} files — write {sentence_range} sentences)\n"]
    for file_path, summary in file_summaries.items():
        parts.append(f"--- {file_path} ---\n{summary}\n")
    return "\n".join(parts)


def summarize_folder(folder: str, file_summaries: dict[str, str]) -> str:
    """Aggregate file summaries into a folder-level summary.

    Parameters
    ----------
    folder:
        Folder path (e.g. "src/api", "src/storage", ".").
    file_summaries:
        dict mapping file_path → summary string for all files in this folder.

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

        context = _build_context(folder, file_summaries)
        query = f"Summarise the `{folder}` folder."

        # No force_model/force_provider — let the full cascade run.
        # Same reasoning as file_summary_agent: avoids a concurrent RPM burst
        # against a single provider when multiple folder agents run in parallel.
        summary, _, prompt_tokens, completion_tokens = _llm.smart_complete(
            query=query,
            context=context,
            system_prompt=_SYSTEM_PROMPT,
            task_type="fast",
        )
        return summary.strip(), prompt_tokens, completion_tokens

    except Exception as exc:
        logger.warning("folder_summary_agent: failed for %s: %s", folder, exc)
        file_names = ", ".join(f"`{fp}`" for fp in list(file_summaries.keys())[:5])
        return f"`{folder}` contains {len(file_summaries)} files: {file_names}.", 0, 0
