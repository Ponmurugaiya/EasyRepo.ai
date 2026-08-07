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
Given summaries of individual source files within a folder, write a 3-5 sentence
folder summary describing:
1. The folder's domain / responsibility (what it owns in the system)
2. The most important files and what they each provide
3. Key patterns across the folder (e.g. all files follow repository pattern, all expose REST routes, etc.)
4. Cross-file dependencies visible from the summaries

Rules:
- Preserve inline citations ([file_path:start-end]) from the file summaries when you mention specific entities.
- Keep the summary to 3-5 sentences maximum.
- Do NOT reproduce source code.
- Do NOT use markdown headers — write flowing prose.
"""


def _build_context(folder: str, file_summaries: dict[str, str]) -> str:
    """Build the context sent to the LLM."""
    parts = [f"Folder: {folder}\n"]
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
    str
        3-5 sentence folder summary with preserved inline citations.
        Returns a minimal fallback on LLM failure.
    """
    if not file_summaries:
        return f"`{folder}` — no files found."

    try:
        import src.generation.llm_client as _llm

        context = _build_context(folder, file_summaries)
        query = f"Summarise the `{folder}` folder."

        summary, _ = _llm.smart_complete(
            query=query,
            context=context,
            system_prompt=_SYSTEM_PROMPT,
            task_type="fast",
            force_model="groq/llama-3.1-8b-instant",
        )
        return summary.strip()

    except Exception as exc:
        logger.warning("folder_summary_agent: failed for %s: %s", folder, exc)
        file_names = ", ".join(f"`{fp}`" for fp in list(file_summaries.keys())[:5])
        return f"`{folder}` contains {len(file_summaries)} files: {file_names}."
