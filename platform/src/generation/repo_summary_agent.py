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
Using the folder summaries provided, write a comprehensive repository overview.

For a BRIEF overview (repository_overview): 4-6 paragraphs covering:
  - What the project does (1 paragraph)
  - Core architecture and key subsystems (2 paragraphs)
  - Main data flows and how components interact (1 paragraph)
  - Entry points and how to navigate the codebase (1 paragraph)

For a DETAILED walkthrough (repository_detailed): Section-per-folder:
  - Each section: folder purpose, key files, main classes/functions
  - More inline citations per section
  - Cross-folder relationships and dependency patterns

Citation rules (CRITICAL):
  - Use ONLY citations that appear in the folder summaries provided.
  - Format: [file_path:start_line-end_line]
  - Do NOT invent file paths or line numbers.
  - If a folder summary contains [auth/service.py:45-89], you may reuse that exact citation.

Do NOT use <answer_json> blocks — write the Markdown answer directly.
"""


def _build_context(
    repo_name: str,
    folder_summaries: dict[str, str],
    total_files: int,
) -> str:
    """Build the context string from folder summaries."""
    folder_parts = [
        f"=== {folder} ===\n{summary}"
        for folder, summary in sorted(folder_summaries.items())
    ]
    arch_hint = (
        f"Repository: {repo_name}  |  "
        f"{total_files} files across {len(folder_summaries)} folders\n\n"
    )
    return arch_hint + "\n\n".join(folder_parts)


def summarize_repo(
    repo_name: str,
    folder_summaries: dict[str, str],
    intent: str,
    query: str,
    total_files: int = 0,
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
    trace:
        Optional PipelineTrace for structured logging.

    Returns
    -------
    tuple[str, str]
        (answer_text, provider_used)
    """
    import src.generation.llm_client as _llm
    from src.generation.llm_client import LLMProviderError

    context = _build_context(repo_name, folder_summaries, total_files)
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
