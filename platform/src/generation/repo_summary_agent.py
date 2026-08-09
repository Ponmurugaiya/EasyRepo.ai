"""Repo Summary Agent.

Synthesises folder-level summaries into a final repository overview answer.
This is the third agent in the hierarchical overview pipeline:

  File Summary Agent → Folder Summary Agent → Repo Summary Agent

Input:  repo name, all folder summaries (with preserved citations), intent, query.
Output: Final Markdown answer with inline [file:line] citations.

Model: Gemini (force_provider="gemini") for best quality on long synthesis.
Falls back to Groq if Gemini is unavailable.

Public API
----------
summarize_repo(repo_name, folder_summaries, intent, query, trace=None)
    -> tuple[str, str]  — (answer_text, provider_used)
"""

from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.pipeline.pipeline_logger import PipelineTrace

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior software architect writing documentation for a codebase.
You will be given:
  1. A PROJECT STRUCTURE tree showing all files and their paths
  2. Folder summaries describing each folder's purpose and key entities

Use BOTH to write a comprehensive repository overview.

For a BRIEF overview (repository_overview): 4-6 paragraphs covering:
  - What the project does (1 paragraph)
  - Core architecture and key subsystems — reference the actual folder/file structure (2 paragraphs)
  - Main data flows and how components interact (1 paragraph)
  - Entry points and how to navigate the codebase — mention specific files by path (1 paragraph)

For a DETAILED walkthrough (repository_detailed): Section-per-folder:
  - Show the folder's files from the project structure
  - Each section: folder purpose, key files with their exact paths, main classes/functions
  - More inline citations per section
  - Cross-folder relationships and dependency patterns

Project structure rules:
  - When describing the layout, use the exact file paths from the PROJECT STRUCTURE tree.
  - You may reproduce the structure tree as a code block in your answer to show the layout.

Citation rules (CRITICAL):
  - Use ONLY citations that appear in the folder summaries provided.
  - Format: [file_path:start_line-end_line]
  - Do NOT invent file paths or line numbers.

Do NOT use <answer_json> blocks — write the Markdown answer directly.
"""


def _build_file_tree(file_paths: list[str]) -> str:
    """Build a directory tree string from a flat list of file paths.

    Input:  ["src/api/main.py", "src/api/auth.py", "src/storage/db.py", "README.md"]
    Output:
        .
        ├── README.md
        └── src/
            ├── api/
            │   ├── auth.py
            │   └── main.py
            └── storage/
                └── db.py
    """
    from collections import defaultdict

    # Normalise separators
    normalised = [p.replace("\\", "/") for p in sorted(file_paths)]

    # Build nested dict tree
    def insert(tree: dict, parts: list[str]) -> None:
        node = tree
        for part in parts:
            node = node.setdefault(part, {})

    tree: dict = {}
    for path in normalised:
        insert(tree, path.split("/"))

    # Render to ASCII art
    lines: list[str] = ["."]

    def render(node: dict, prefix: str) -> None:
        items = sorted(node.keys())
        for i, name in enumerate(items):
            is_last = i == len(items) - 1
            connector = "└── " if is_last else "├── "
            child = node[name]
            if child:
                # directory
                lines.append(f"{prefix}{connector}{name}/")
                extension = "    " if is_last else "│   "
                render(child, prefix + extension)
            else:
                # file
                lines.append(f"{prefix}{connector}{name}")

    render(tree, "")
    return "\n".join(lines)


def _build_context(
    repo_name: str,
    folder_summaries: dict[str, str],
    total_files: int,
    file_paths: Optional[list[str]] = None,
) -> str:
    """Build the context string: file tree + folder summaries."""
    parts: list[str] = []

    # 1. Repository header
    parts.append(
        f"Repository: {repo_name}  |  "
        f"{total_files} files across {len(folder_summaries)} folders"
    )

    # 2. Full project structure tree
    if file_paths:
        tree = _build_file_tree(file_paths)
        parts.append(
            f"\n=== PROJECT STRUCTURE ===\n{tree}\n=== END PROJECT STRUCTURE ==="
        )

    # 3. Folder summaries with citations
    folder_parts = [
        f"=== {folder} ===\n{summary}"
        for folder, summary in sorted(folder_summaries.items())
    ]
    parts.append("\n\n".join(folder_parts))

    return "\n\n".join(parts)


def summarize_repo(
    repo_name: str,
    folder_summaries: dict[str, str],
    intent: str,
    query: str,
    total_files: int = 0,
    file_paths: Optional[list[str]] = None,
    trace: Optional["PipelineTrace"] = None,
) -> tuple[str, str]:
    """Synthesise folder summaries into a final repository overview answer.

    Parameters
    ----------
    repo_name:
        Repository display name.
    folder_summaries:
        dict mapping folder_path → summary string (with citations preserved).
    intent:
        "repository_overview" or "repository_detailed" — controls verbosity.
    query:
        Original user query (included in the LLM prompt for grounding).
    total_files:
        Total number of files in the repo (for the architecture hint).
    file_paths:
        All file paths indexed in the repo — rendered as a project structure
        tree prepended to the context so the LLM can describe the layout.
    trace:
        Optional PipelineTrace for structured logging.

    Returns
    -------
    tuple[str, str]
        (answer_text, provider_used)
    """
    import src.generation.llm_client as _llm
    from src.generation.llm_client import LLMProviderError

    context = _build_context(repo_name, folder_summaries, total_files, file_paths)
    context_tokens = len(context) // 4
    mode = "detailed" if intent == "repository_detailed" else "brief"
    repo_query = f"Write a {mode} overview of this repository. Query: {query}"

    # Adjust system prompt for brief vs detailed
    system = _SYSTEM_PROMPT.replace(
        "For a BRIEF overview (repository_overview):",
        f"For a {'BRIEF' if mode == 'brief' else 'DETAILED'} overview ({intent}):",
    )

    if trace:
        trace.step_llm_dispatch(
            attempt=0,
            model="gemini-2.5-flash",
            provider="gemini",
            context_tokens=context_tokens,
            task_type="repo_summary",
        )

    t0 = time.monotonic()
    try:
        answer, provider, prompt_tokens, completion_tokens = _llm.smart_complete(
            query=repo_query,
            context=context,
            system_prompt=system,
            task_type="standard",
            force_provider="gemini",
        )
        elapsed_ms = (time.monotonic() - t0) * 1000
        if trace:
            trace.step_llm_response(
                provider=provider,
                model="gemini-2.5-flash",
                answer_raw=answer,
                status="answered",
                elapsed_ms=elapsed_ms,
                input_tokens=prompt_tokens,
            )
        logger.info(
            "RepoSummaryAgent: done — %d chars via %s (in=%d out=%d tok)",
            len(answer), provider, prompt_tokens, completion_tokens,
        )
        return answer, provider

    except LLMProviderError:
        # Gemini unavailable — fall back to Groq
        try:
            answer, provider, prompt_tokens, completion_tokens = _llm.smart_complete(
                query=repo_query,
                context=context,
                system_prompt=system,
                task_type="standard",
                force_provider="groq",
            )
            elapsed_ms = (time.monotonic() - t0) * 1000
            if trace:
                trace.step_llm_response(
                    provider=provider,
                    model="auto",
                    answer_raw=answer,
                    status="answered",
                    elapsed_ms=elapsed_ms,
                    input_tokens=prompt_tokens,
                )
            logger.info("RepoSummaryAgent: Gemini failed, used Groq fallback")
            return answer, provider
        except Exception as exc2:
            elapsed_ms = (time.monotonic() - t0) * 1000
            logger.error("RepoSummaryAgent: all providers failed: %s", exc2)
            if trace:
                trace.step_llm_response(
                    provider="unknown", model="unknown",
                    answer_raw="", status="error", elapsed_ms=elapsed_ms,
                )
            raise

    except Exception as exc:
        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.error("RepoSummaryAgent: failed: %s", exc)
        if trace:
            trace.step_llm_response(
                provider="unknown", model="gemini-2.5-flash",
                answer_raw="", status="error", elapsed_ms=elapsed_ms,
            )
        raise
