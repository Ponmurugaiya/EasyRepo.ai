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
You are a senior software architect writing a high-level overview of a codebase.
You will be given:
  1. A PROJECT STRUCTURE tree showing every file and its exact path
  2. Folder summaries describing each folder's purpose and key entities

Write a concise, readable overview in 4-6 paragraphs:

Paragraph 1 — What the project does
  Describe the project's purpose, the problem it solves, and its primary users.

Paragraphs 2-3 — Core architecture and key subsystems
  Identify the main subsystems/layers (e.g. API, storage, generation, ingestion).
  Reference specific folders and files from the PROJECT STRUCTURE by their exact paths.
  Example: "The API layer lives in `src/api/` with the main entry point at `src/api/main.py`."

Paragraph 4 — Main data flows
  Describe how data moves through the system end-to-end.
  Mention the key files involved at each stage.

Paragraph 5 — Entry points and navigation
  Tell a developer where to start reading:
  - Which files are the main entry points (name them with exact paths)
  - How the folders map to concerns
  - You MAY reproduce the project structure tree as a code block so the reader can see the layout.

Citation rules (CRITICAL):
  - Use ONLY citations that already appear verbatim in the folder summaries — copy them exactly.
  - Citation format is [file_path:start_line-end_line] where start_line and end_line are INTEGERS.
  - NEVER invent file paths, line numbers, or write placeholder words like "start" or "end".
  - File paths in prose (not citations) must exactly match the PROJECT STRUCTURE tree.

Do NOT use <answer_json> blocks — write Markdown prose directly.
"""

_SYSTEM_PROMPT_DETAILED = """\
You are a senior software architect writing a detailed technical walkthrough of a codebase.
You will be given:
  1. A PROJECT STRUCTURE tree showing every file and its exact path
  2. Folder summaries describing each folder's purpose and key entities

Start with a brief project introduction (2-3 sentences), then write one section per folder.

For each folder section:
  ### `folder/path/` — Brief folder title

  List the files in this folder (use the exact paths from the PROJECT STRUCTURE tree):
  ```
  folder/path/
  ├── file1.py
  └── file2.py
  ```

  Then describe:
  - The folder's responsibility and domain ownership
  - Each key file: what it does, main classes/functions with inline citations
  - Cross-folder dependencies: what this folder imports from or exports to others

Cross-reference sections:
  After all folder sections, add a "Key Data Flows" section tracing 1-3 important
  end-to-end paths through the codebase (e.g. request → processing → response).

Citation rules (CRITICAL):
  - Use ONLY citations that already appear verbatim in the folder summaries — copy them exactly.
  - Citation format is [file_path:start_line-end_line] where start_line and end_line are INTEGERS.
  - NEVER invent file paths, line numbers, or write placeholder words like "start" or "end".
  - File paths in prose and code blocks must exactly match the PROJECT STRUCTURE tree.

Do NOT use <answer_json> blocks — write Markdown directly.
"""


def _get_system_prompt(intent: str) -> str:
    """Return the appropriate system prompt for the given intent."""
    if intent == "repository_detailed":
        return _SYSTEM_PROMPT_DETAILED
    return _SYSTEM_PROMPT_OVERVIEW


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
    mode = "detailed" if intent == "repository_detailed" else "overview"
    repo_query = f"Write a repository {mode} for this codebase. User query: {query}"

    # Each intent gets its own focused system prompt
    system = _get_system_prompt(intent)

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
