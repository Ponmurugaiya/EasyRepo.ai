"""Repository Walk retrieval module.

Produces an architecture-level view of the repository without relying on a
user query for vector search.  Used when the Query Planner selects the
``repository_walk`` strategy (e.g. "how is this repo structured?").

This module wraps the existing file graph infrastructure:
  - build_file_graph  (src/graph/file_graph.py)
  - detect_entry_points (src/graph/entry_points.py)
  - traverse / get_full_graph (src/graph/traversal.py)

The result is converted into a list of RetrievalResult objects so the rest of
the pipeline (expand → build_context) works without modification.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from src.graph.entry_points import EntryPointResult
from src.graph.file_graph import FileEdge, FileNode, build_file_graph
from src.graph.traversal import GraphNode, get_full_graph, traverse
from src.retrieval.models import RetrievalResult
from src.storage.models import EntityModel

logger = logging.getLogger(__name__)

# Maximum number of files to pull entities from for context assembly.
# Keeps the context from exploding on very large repos.
_TOP_FILES_LIMIT = 10


@dataclass
class RepoWalkResult:
    """Result of a repository walk.

    Attributes
    ----------
    entry_points:
        Detected entry points with scores.
    modules:
        All discovered file nodes (may be depth-limited).
    edges:
        Cross-file relationship edges in the traversed subgraph.
    top_entities:
        Module-level entities from the highest-scoring files.
    architecture_summary_hint:
        Short text hint passed to the Answer Agent explaining the repo structure.
    """

    entry_points: list[EntryPointResult]
    modules: list[GraphNode]
    edges: list[FileEdge]
    top_entities: list[EntityModel]
    architecture_summary_hint: str


def walk(repo_id: str, db: Session, max_depth: int = 3) -> RepoWalkResult:
    """Build an architecture-level view of the repository.

    Steps:
    1. Build the file-level graph.
    2. Detect entry points.
    3. BFS-traverse from the top entry point (or use full graph if none found).
    4. Fetch module-level entities for the top-N files.
    5. Construct a text summary hint describing the repo structure.

    Parameters
    ----------
    repo_id:
        Target repository ID.
    db:
        Active SQLAlchemy database session.
    max_depth:
        Maximum BFS depth from the entry point.

    Returns
    -------
    RepoWalkResult
    """
    # Step 1 + 2: build graph (entry point detection is embedded inside)
    graph = build_file_graph(repo_id, db)

    if not graph.nodes:
        logger.warning("repo_walk: no file nodes found for repo %s", repo_id)
        return RepoWalkResult(
            entry_points=[],
            modules=[],
            edges=[],
            top_entities=[],
            architecture_summary_hint="No files were found in the repository index.",
        )

    # Step 3: traverse from root entry point, or take the full graph
    if graph.root:
        subgraph = traverse(graph, root_id=graph.root, max_depth=max_depth)
    else:
        subgraph = get_full_graph(graph, exclude_orphans=False)

    # Rank nodes by entry_score desc, then entity_count desc
    ranked_nodes = sorted(
        subgraph.nodes,
        key=lambda n: (n.entry_score, n.is_entry, n.is_root),
        reverse=True,
    )

    # Step 4: fetch entities for the top N files
    top_file_ids = [n.id for n in ranked_nodes[:_TOP_FILES_LIMIT]]

    top_entities: list[EntityModel] = []
    if top_file_ids:
        # Fetch module entities themselves (type == "module") for top files
        top_entities = (
            db.query(EntityModel)
            .filter(
                EntityModel.repo_id == repo_id,
                EntityModel.id.in_(top_file_ids),
            )
            .all()
        )
        # Also fetch a sample of non-module entities (functions, classes)
        # from those files to give the LLM structural context.
        child_entities: list[EntityModel] = (
            db.query(EntityModel)
            .filter(
                EntityModel.repo_id == repo_id,
                EntityModel.file_path.in_([n.file_path for n in ranked_nodes[:_TOP_FILES_LIMIT]]),
                EntityModel.type.in_(["function", "class", "interface"]),
            )
            .limit(30)
            .all()
        )
        # Merge, deduplicating by ID
        seen_ids = {e.id for e in top_entities}
        for ent in child_entities:
            if ent.id not in seen_ids:
                top_entities.append(ent)
                seen_ids.add(ent.id)

    # Step 5: build human-readable architecture hint
    hint_lines = [
        f"Repository has {len(graph.nodes)} files and {len(graph.edges)} cross-file edges.",
    ]
    if graph.entry_points:
        ep_names = ", ".join(ep.file_path for ep in graph.entry_points[:3])
        hint_lines.append(f"Top entry points: {ep_names}")
    if subgraph.nodes:
        file_names = ", ".join(n.name for n in ranked_nodes[:5])
        hint_lines.append(f"Key files (by entry score): {file_names}")

    architecture_summary_hint = " ".join(hint_lines)

    return RepoWalkResult(
        entry_points=graph.entry_points,
        modules=subgraph.nodes,
        edges=subgraph.edges,
        top_entities=top_entities,
        architecture_summary_hint=architecture_summary_hint,
    )


def to_retrieval_results(walk_result: RepoWalkResult) -> list[RetrievalResult]:
    """Convert a RepoWalkResult into RetrievalResult objects.

    Assigns synthetic scores so the rest of the pipeline (expand, build_context)
    works without modification.  Module entities get the highest scores;
    child entities get lower scores so they're deprioritised in context assembly.

    Parameters
    ----------
    walk_result:
        Output of ``walk()``.

    Returns
    -------
    list[RetrievalResult]
        Ranked list suitable for passing to ``expand()`` and ``build_context()``.
    """
    results: list[RetrievalResult] = []

    # Build a set of module entity IDs for score assignment
    module_ids = {ep.entity_id for ep in walk_result.entry_points}

    for rank, entity in enumerate(walk_result.top_entities, start=1):
        # Entry point modules get higher synthetic scores
        if entity.id in module_ids:
            score = 1.0 - (rank * 0.01)
        elif entity.type == "module":
            score = 0.8 - (rank * 0.01)
        else:
            score = 0.6 - (rank * 0.01)

        score = max(0.01, score)

        results.append(
            RetrievalResult(
                entity_id=entity.id,
                entity=entity,
                score=score,
                rank=rank,
            )
        )

    return results
