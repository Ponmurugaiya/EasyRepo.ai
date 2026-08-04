"""BFS traversal over the file-level graph.

Given a root file node, performs a breadth-first traversal following
outgoing edges up to ``max_depth`` hops.  Returns the subgraph (nodes +
edges) reachable from the root, with depth metadata attached to nodes.

Edge type priority for traversal:
  CALLS and INHERITS are followed first (execution/structural).
  IMPORTS are followed second (dependency structure).
  INSTANTIATES follows after.

The traversal is directional — it only follows outgoing edges, so the
result represents "things that *root* depends on", which maps to
execution order (root → callee → callee's callee ...).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from src.graph.file_graph import FileEdge, FileGraph, FileNode


# Edge types to follow during traversal, in priority order
_TRAVERSAL_TYPES = {"CALLS", "INHERITS", "IMPLEMENTS", "INSTANTIATES", "IMPORTS"}


@dataclass
class GraphNode:
    """A FileNode enriched with traversal metadata."""
    id: str
    file_path: str
    name: str
    language: str
    entity_count: int
    is_entry: bool
    entry_score: int
    depth: int = 0          # hops from root (root = 0)
    is_root: bool = False


@dataclass
class Subgraph:
    """The trimmed graph reachable from a root node."""
    root_id: str
    nodes: list[GraphNode]
    edges: list[FileEdge]


def traverse(
    graph: FileGraph,
    root_id: str,
    max_depth: int = 4,
    include_imports: bool = True,
) -> Subgraph:
    """BFS from *root_id*, returning all reachable nodes within *max_depth*.

    Args:
        graph:           Full file graph for the repository.
        root_id:         Entity ID of the root module to start from.
        max_depth:       Maximum hops to follow.  0 = root only.
        include_imports: If False, IMPORTS edges are not traversed
                         (shows only CALLS/INHERITS/INSTANTIATES tree).

    Returns:
        :class:`Subgraph` with only the reachable nodes and the edges
        between them.
    """
    if root_id not in graph.nodes:
        # Root not found — return empty subgraph
        return Subgraph(root_id=root_id, nodes=[], edges=[])

    # Edge types to follow
    follow_types = set(_TRAVERSAL_TYPES)
    if not include_imports:
        follow_types.discard("IMPORTS")

    # Build adjacency: source_file_id → list of (target_file_id, edge)
    outgoing: dict[str, list[FileEdge]] = {}
    for edge in graph.edges:
        outgoing.setdefault(edge.source_file_id, []).append(edge)

    # BFS
    visited: dict[str, int] = {}          # node_id → depth first seen
    queue: deque[tuple[str, int]] = deque()
    queue.append((root_id, 0))
    visited[root_id] = 0

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue

        for edge in outgoing.get(current_id, []):
            # Only follow edges of relevant types
            edge_rel_types = set(edge.rel_types)
            if not edge_rel_types.intersection(follow_types):
                continue

            target = edge.target_file_id
            if target not in visited:
                visited[target] = depth + 1
                queue.append((target, depth + 1))

    # Collect nodes in visited set
    result_nodes: list[GraphNode] = []
    for node_id, depth in visited.items():
        fn = graph.nodes.get(node_id)
        if fn is None:
            continue
        result_nodes.append(
            GraphNode(
                id=fn.id,
                file_path=fn.file_path,
                name=fn.name,
                language=fn.language,
                entity_count=fn.entity_count,
                is_entry=fn.is_entry,
                entry_score=fn.entry_score,
                depth=depth,
                is_root=(node_id == root_id),
            )
        )

    result_nodes.sort(key=lambda n: (n.depth, n.name))

    # Collect only edges where both endpoints are in the visited set
    visited_ids = set(visited.keys())
    result_edges = [
        e for e in graph.edges
        if e.source_file_id in visited_ids and e.target_file_id in visited_ids
    ]

    return Subgraph(root_id=root_id, nodes=result_nodes, edges=result_edges)


def get_full_graph(graph: FileGraph, exclude_orphans: bool = True) -> Subgraph:
    """Return the entire graph without depth-limiting traversal.

    Args:
        graph:           Full file graph for the repository.
        exclude_orphans: If True, omit file nodes that have no edges at all.
    """
    if not graph.nodes:
        return Subgraph(root_id="", nodes=[], edges=graph.edges)

    connected_ids: set[str] = set()
    for edge in graph.edges:
        connected_ids.add(edge.source_file_id)
        connected_ids.add(edge.target_file_id)

    result_nodes: list[GraphNode] = []
    for fn in graph.nodes.values():
        if exclude_orphans and fn.id not in connected_ids:
            continue
        result_nodes.append(
            GraphNode(
                id=fn.id,
                file_path=fn.file_path,
                name=fn.name,
                language=fn.language,
                entity_count=fn.entity_count,
                is_entry=fn.is_entry,
                entry_score=fn.entry_score,
                depth=0,
                is_root=fn.is_entry,
            )
        )

    root_id = graph.root or ""  # empty string = no real entry point detected
    return Subgraph(root_id=root_id, nodes=result_nodes, edges=graph.edges)
