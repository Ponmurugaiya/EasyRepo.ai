"""Short-Term Memory (STM) dataclass for the unified query pipeline.

Created at the start of each request, passed through every pipeline stage,
and discarded when the response is sent.  Never persisted to the database.

The STM acts as the shared mutable state object that lets stages communicate
without tight coupling — each stage reads what it needs and writes its outputs
back into the same object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.retrieval.models import ExpandedContext


@dataclass
class ShortTermMemory:
    """Holds the full reasoning state for one pipeline request.

    Attributes
    ----------
    goal:
        The original user query, never modified.
    intent:
        Query intent classified by the planner (e.g. "feature", "repository_overview").
    retrieval_strategy:
        Strategy selected by the planner:
        "semantic_search" | "semantic_search_with_graph" | "repository_walk"
    search_query:
        Rewritten query for code embeddings (may differ from goal).
        Defaults to goal when the planner does not rewrite.
    session_id:
        Optional client UUID for LTM scoping.  None = LTM disabled.
    repo_id:
        Target repository identifier.
    visited_entity_ids:
        All entity IDs seen so far — used to deduplicate re-retrieval.
    pending_entity_ids:
        Entities flagged for future expansion (reserved for future use).
    retrieved_chunks:
        Accumulated ExpandedContext objects across all retrieval iterations.
    intermediate_summaries:
        Summaries written by agents during the pipeline run.
    answer_status:
        Last answer status from the Answer Agent:
        "answered" | "insufficient" | "rewrite_search"
    answer_text:
        Final answer text (set when answer_status == "answered" or on best-effort fallback).
    missing:
        Structured hint from the Answer Agent about what context is missing.
        Format: {"type": "...", "entity": "..."}
    rewrite_query:
        New search query when answer_status == "rewrite_search".
    iteration_count:
        Number of targeted re-retrieval iterations completed.
        Capped at 2 to prevent infinite loops.
    """

    goal: str
    repo_id: str

    # Planner outputs (populated after planning step)
    intent: str = "query"
    retrieval_strategy: str = "semantic_search"
    search_query: str | None = None

    # Session context
    session_id: str | None = None
    conversation_id: str | None = None

    # Accumulated context
    visited_entity_ids: set[str] = field(default_factory=set)
    pending_entity_ids: set[str] = field(default_factory=set)
    retrieved_chunks: list["ExpandedContext"] = field(default_factory=list)
    intermediate_summaries: list[str] = field(default_factory=list)

    # Answer Agent outputs
    answer_status: str = "pending"
    answer_text: str | None = None
    missing: dict | None = None
    rewrite_query: str | None = None

    # Re-retrieval loop counter
    iteration_count: int = 0

    # ── Overview pipeline additions ──────────────────────────────────────────
    # Populated by repo_overview.py when intent is repository_overview/detailed.
    # Key = canonical file_path as stored in EntityModel.file_path
    file_summaries: dict[str, str] = field(default_factory=dict)
    # Key = folder path (e.g. "src/api", "src/storage", ".")
    folder_summaries: dict[str, str] = field(default_factory=dict)
    # True when the final answer was served entirely from LTM (no agents ran)
    overview_from_cache: bool = False
