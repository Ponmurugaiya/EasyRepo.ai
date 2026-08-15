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

_SYSTEM_PROMPT_OVERVIEW = """\
You are a senior software architect answering a developer's question about a codebase.

You are given:
- A PROJECT STRUCTURE tree (every file with its exact path)
- Folder summaries (each folder's purpose, key files, and inline citations)

Your task: use these resources to write a clear, structured overview a developer can scan quickly.

Output format — write in Markdown using this structure:
  ## What this project does
  - One bullet: the project's purpose and the problem it solves
  - One bullet: who uses it / primary use case

  ## Architecture
  - One bullet per main folder/layer — bold the folder path, then describe what it owns
  - For the folder labelled "(root)", describe it as the top-level directory using the repo name
  - Example: **`src/api/`** — HTTP layer, handles requests and Cognito auth

  ## Key entry points
  - One bullet per important file — file path in backticks, short description, inline citation if available
  - Example: `src/api/main.py` — bootstraps the app and registers all routes [src/api/main.py:1-30]

  ## Data flow
  - Numbered steps describing how a request moves through the system end-to-end
  - Keep each step to one line

Output rules:
- Use bullet points and numbered lists. Do NOT write long paragraphs.
- Do NOT add labels like "Paragraph 1" or any section numbering.
- Do NOT add any section not listed above.
- File paths in prose and bullets must exactly match the PROJECT STRUCTURE tree.

Citation rules (CRITICAL — do not break these):
- Copy citations VERBATIM from the folder summaries — do not retype or modify them.
- Format is [file_path:start_line-end_line] where both line numbers are INTEGERS.
- Do NOT invent citations, file paths, or line numbers.
- Do NOT use placeholder words like "start" or "end" as line numbers.
"""

_SYSTEM_PROMPT_DETAILED = """\
You are a senior software architect answering a developer's question about a codebase.

You are given:
- A PROJECT STRUCTURE tree (every file with its exact path)
- Folder summaries (each folder's purpose, key files, and inline citations)

Your task: write a detailed technical walkthrough a developer can use to navigate the codebase.

Output format — write in Markdown using this structure:
  ## Overview
  - 2-3 bullets summarising the whole project (purpose, stack, main concerns)

  ### `folder/path/` — short title describing the folder's role
  - For the folder labelled "(root)", use the repo name as the section title
  - One bullet per file: file path in backticks, what it does, inline citation if available
  - One bullet for cross-folder dependencies (what this folder imports from or exports to others)
  (repeat one section per folder)

  ## Key data flows
  1. Numbered steps for one important end-to-end path (e.g. a user query from request to response)

Output rules:
- Use bullet points inside every section. Do NOT write paragraphs.
- Use ### for folder sections, ## for top-level sections.
- Do NOT add labels like "Paragraph 1" or any section numbering.
- File paths in prose and bullets must exactly match the PROJECT STRUCTURE tree.

Citation rules (CRITICAL — do not break these):
- Copy citations VERBATIM from the folder summaries — do not retype or modify them.
- Format is [file_path:start_line-end_line] where both line numbers are INTEGERS.
- Do NOT invent citations, file paths, or line numbers.
- Do NOT use placeholder words like "start" or "end" as line numbers.
"""


def _get_system_prompt(intent: str, repo_language_note: str | None = None) -> str:
    """Return the appropriate system prompt for the given intent.

    Appends a language coverage note when the repository contains files in
    languages that are not yet supported (e.g. Java, Go, Ruby).  This keeps
    the LLM honest about the scope of the indexed data.
    """
    base = _SYSTEM_PROMPT_DETAILED if intent == "repository_detailed" else _SYSTEM_PROMPT_OVERVIEW
    if not repo_language_note:
        return base
    return (
        base
        + f"\n\n# Repository language coverage\n{repo_language_note}\n"
        "When writing the overview, include a brief note (one bullet under the "
        "Architecture or Overview section) stating which languages are indexed "
        "(Python and TypeScript) and which others exist in the project but are "
        "not yet indexed.  Do not fabricate details about the unindexed files."
    )


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
    # Rename '.' to '(root)' so the LLM doesn't output a literal dot as a section title
    folder_parts = [
        f"=== {folder if folder != '.' else '(root)'} ===\n{summary}"
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
    repo_language_note: Optional[str] = None,
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
    repo_language_note:
        Optional note about partial language coverage.  When the repo contains
        files in unsupported languages (Java, Go, Ruby, …) alongside Python/TS,
        this note is injected into the system prompt so the LLM can surface that
        context in the final overview without fabricating details.

    Returns
    -------
    tuple[str, str]
        (answer_text, provider_used)
    """
    import src.generation.llm_client as _llm
    from src.generation.llm_client import LLMProviderError

    context = _build_context(repo_name, folder_summaries, total_files, file_paths)
    context_tokens = len(context) // 4
    mode = "detailed" if intent == "repository_detailed" else "overview"
    repo_query = f"Write a repository {mode} for this codebase. User query: {query}"

    # Each intent gets its own focused system prompt, optionally extended with
    # a language coverage note for repos that contain unsupported languages.
    system = _get_system_prompt(intent, repo_language_note=repo_language_note)

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
        # Gemini unavailable — try Groq standard next
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
        except LLMProviderError:
            # Groq also exhausted — fall through to free cascade (NVIDIA NIM,
            # Cloudflare, OpenRouter) downgrading to fast tier if needed.
            try:
                answer, provider, prompt_tokens, completion_tokens = _llm.smart_complete(
                    query=repo_query,
                    context=context,
                    system_prompt=system,
                    task_type="standard",
                    skip_providers={"gemini", "groq", "cerebras", "cohere"},
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
                logger.info("RepoSummaryAgent: Gemini+Groq failed, used %s fallback", provider)
                return answer, provider
            except LLMProviderError:
                # Last resort — downgrade to fast tier (NVIDIA NIM / Cloudflare)
                try:
                    answer, provider, prompt_tokens, completion_tokens = _llm.smart_complete(
                        query=repo_query,
                        context=context,
                        system_prompt=system,
                        task_type="fast",
                        skip_providers={"gemini", "groq", "cerebras", "cohere", "openrouter"},
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
                    logger.info(
                        "RepoSummaryAgent: all standard providers failed, used fast-tier %s",
                        provider,
                    )
                    return answer, provider
                except Exception as exc3:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    logger.error("RepoSummaryAgent: all providers failed: %s", exc3)
                    if trace:
                        trace.step_llm_response(
                            provider="unknown", model="unknown",
                            answer_raw="", status="error", elapsed_ms=elapsed_ms,
                        )
                    raise LLMProviderError(str(exc3)) from exc3

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
