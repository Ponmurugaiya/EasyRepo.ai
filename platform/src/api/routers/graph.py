"""Graph router — file-level code graph endpoints.

GET /repositories/{repo_id}/graph
    Returns the file-level graph with entities embedded inside each file node.
    No separate expand call needed — the frontend has everything to render
    files as content-rich nodes from the start.

GET /repositories/{repo_id}/graph/{file_entity_id}/expand
    Returns full source + detailed entity list for a single file.
    Used when the user clicks an entity row to open the code viewer.
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.api.dependencies import get_accessible_repository, get_current_user, get_db
from src.api.schemas import (
    EntityConnectionSchema,
    ExpandedEntitySchema,
    FileEdgeSchema,
    FileExpandResponse,
    FileGraphResponse,
    FileNodeSchema,
    InlineEntitySchema,
)
from src.graph.file_graph import EntityConnection, FileEdge, FileNode, _TYPE_RANK
from src.graph.traversal import get_full_graph, traverse
from src.graph.file_graph import build_file_graph
from src.storage.models import EntityModel, RelationshipModel, UserModel

router = APIRouter()

_limiter = Limiter(key_func=get_remote_address)
_RATE_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")

# Entity types to show inside file nodes (exclude module — that IS the file node)
_SHOW_IN_NODE = {"class", "interface", "function", "method", "variable"}


@router.get("/{repo_id}/graph", response_model=FileGraphResponse)
@_limiter.limit(_RATE_DEFAULT)
async def get_file_graph(
    request: Request,
    repo_id: str,
    root: Optional[str] = None,
    depth: int = 4,
    include_imports: bool = True,
    show_all: bool = False,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> FileGraphResponse:
    """Return the file-level graph with entities embedded in each node.

    Every file node in the response already contains its entity list —
    no separate expand call required.

    Query params:
    - ``root``            Entity ID of the file to start traversal from.
    - ``depth``           BFS depth limit (default 4).
    - ``include_imports`` Whether to follow IMPORTS edges (default true).
    - ``show_all``        Return all connected files, ignoring depth.
    """
    repo = get_accessible_repository(repo_id, db, current_user)
    if repo.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Repository '{repo_id}' is not ready (status: {repo.status})",
        )

    # ── Build the file graph ────────────────────────────────────────────────
    graph = build_file_graph(repo_id, db)

    if not graph.nodes:
        return FileGraphResponse(root=None, entry_points=[], nodes=[], edges=[])

    # ── Resolve traversal root ──────────────────────────────────────────────
    resolved_root = root or graph.root
    if resolved_root and resolved_root not in graph.nodes:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File node '{resolved_root}' not found in repository graph.",
        )

    if show_all or resolved_root is None:
        subgraph = get_full_graph(graph, exclude_orphans=not show_all)
    else:
        subgraph = traverse(
            graph=graph,
            root_id=resolved_root,
            max_depth=max(1, min(depth, 10)),
            include_imports=include_imports,
        )

    # ── Fetch ALL entities for files in the subgraph in one query ──────────
    node_ids_in_subgraph = {n.id for n in subgraph.nodes}
    entities_per_file: dict[str, list[EntityModel]] = defaultdict(list)
    # source text keyed by module entity id
    source_per_file: dict[str, str] = {}

    if node_ids_in_subgraph:
        file_paths_in_subgraph = {
            graph.nodes[nid].file_path
            for nid in node_ids_in_subgraph
            if nid in graph.nodes
        }
        file_to_module = {
            graph.nodes[nid].file_path: nid
            for nid in node_ids_in_subgraph
            if nid in graph.nodes
        }

        # Fetch module entities to get full source text
        module_entities: list[EntityModel] = (
            db.query(EntityModel)
            .filter(
                EntityModel.repo_id == repo_id,
                EntityModel.file_path.in_(file_paths_in_subgraph),
                EntityModel.type == "module",
            )
            .all()
        )
        for mod in module_entities:
            mod_id = file_to_module.get(mod.file_path)
            if mod_id:
                source_per_file[mod_id] = mod.source or ""

        # Fetch child entities (classes, functions, methods, variables)
        child_entities: list[EntityModel] = (
            db.query(EntityModel)
            .filter(
                EntityModel.repo_id == repo_id,
                EntityModel.file_path.in_(file_paths_in_subgraph),
                EntityModel.type.in_(_SHOW_IN_NODE),
            )
            .order_by(EntityModel.file_path, EntityModel.start_line)
            .all()
        )

        for ent in child_entities:
            mod_id = file_to_module.get(ent.file_path)
            if mod_id:
                entities_per_file[mod_id].append(ent)

    # ── Serialize nodes with embedded entities + source ─────────────────────
    nodes = []
    for n in subgraph.nodes:
        inline_entities = [
            InlineEntitySchema(
                id=e.id,
                name=e.name,
                type=e.type,
                start_line=e.start_line,
                end_line=e.end_line,
                has_docstring=e.has_docstring,
            )
            for e in entities_per_file.get(n.id, [])
        ]
        nodes.append(
            FileNodeSchema(
                id=n.id,
                file_path=n.file_path,
                name=n.name,
                language=n.language,
                is_entry=n.is_entry,
                entry_score=n.entry_score,
                depth=n.depth,
                is_root=n.is_root,
                source=source_per_file.get(n.id, ""),
                entities=inline_entities,
            )
        )

    # ── Serialize edges ─────────────────────────────────────────────────────
    edges = [
        FileEdgeSchema(
            source_file_id=e.source_file_id,
            target_file_id=e.target_file_id,
            rel_types=e.rel_types,
            dominant_type=e.dominant_type,
            connections=[
                EntityConnectionSchema(
                    from_entity_id=c.from_entity_id,
                    from_entity_name=c.from_entity_name,
                    to_entity_id=c.to_entity_id,
                    to_entity_name=c.to_entity_name,
                    rel_type=c.rel_type,
                    line=c.line,
                )
                for c in e.connections
            ],
        )
        for e in subgraph.edges
    ]

    return FileGraphResponse(
        root=subgraph.root_id,
        entry_points=[ep.entity_id for ep in graph.entry_points],
        nodes=nodes,
        edges=edges,
    )


@router.get(
    "/{repo_id}/graph/{file_entity_id:path}/expand",
    response_model=FileExpandResponse,
)
@_limiter.limit(_RATE_DEFAULT)
async def expand_file_node(
    request: Request,
    repo_id: str,
    file_entity_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> FileExpandResponse:
    """Return full entity detail + cross-file edges for a single file.

    Used when the user clicks an entity row to view its source code or
    inspect its specific cross-file connections.
    """
    get_accessible_repository(repo_id, db, current_user)

    file_entity = db.query(EntityModel).filter_by(
        id=file_entity_id, repo_id=repo_id, type="module"
    ).first()
    if not file_entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File node '{file_entity_id}' not found in repository '{repo_id}'.",
        )

    children: list[EntityModel] = (
        db.query(EntityModel)
        .filter(
            EntityModel.repo_id == repo_id,
            EntityModel.file_path == file_entity.file_path,
            EntityModel.type.in_(_SHOW_IN_NODE),
        )
        .order_by(EntityModel.start_line)
        .all()
    )

    child_ids = {c.id for c in children} | {file_entity_id}

    outgoing_rels = (
        db.query(RelationshipModel)
        .filter(
            RelationshipModel.repo_id == repo_id,
            RelationshipModel.source_id.in_(child_ids),
            RelationshipModel.type.in_(
                ["CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "INSTANTIATES"]
            ),
            RelationshipModel.target_id.isnot(None),
        )
        .all()
    )

    incoming_rels = (
        db.query(RelationshipModel)
        .filter(
            RelationshipModel.repo_id == repo_id,
            RelationshipModel.target_id.in_(child_ids),
            RelationshipModel.type.in_(
                ["CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "INSTANTIATES"]
            ),
        )
        .all()
    )

    # Build name lookup
    name_lookup: dict[str, str] = {c.id: c.name for c in children}
    name_lookup[file_entity_id] = file_entity.name

    external_ids = set()
    for rel in outgoing_rels + incoming_rels:
        external_ids.add(rel.source_id)
        if rel.target_id:
            external_ids.add(rel.target_id)
    for eid, ename in (
        db.query(EntityModel.id, EntityModel.name)
        .filter(EntityModel.repo_id == repo_id, EntityModel.id.in_(external_ids - child_ids))
        .all()
    ):
        name_lookup[eid] = ename

    def _name(eid: str) -> str:
        return name_lookup.get(eid, eid.split(".")[-1])

    def _is_cross_file(target_id: Optional[str]) -> bool:
        if not target_id:
            return False
        t = db.query(EntityModel.file_path).filter_by(id=target_id, repo_id=repo_id).first()
        return t is not None and t.file_path != file_entity.file_path

    def _dedup(rels) -> list[EntityConnectionSchema]:
        seen: set[tuple[str, str, str]] = set()
        result = []
        for r in rels:
            if not r.target_id:
                continue
            k = (r.source_id, r.target_id, r.type)
            if k not in seen:
                seen.add(k)
                result.append(EntityConnectionSchema(
                    from_entity_id=r.source_id,
                    from_entity_name=_name(r.source_id),
                    to_entity_id=r.target_id,
                    to_entity_name=_name(r.target_id),
                    rel_type=r.type,
                    line=r.line,
                ))
        return result

    cross_out = [r for r in outgoing_rels if _is_cross_file(r.target_id)]
    cross_in = [r for r in incoming_rels if r.source_id not in child_ids]

    return FileExpandResponse(
        file_id=file_entity_id,
        file_path=file_entity.file_path,
        entities=[
            ExpandedEntitySchema(
                id=c.id, name=c.name, type=c.type,
                start_line=c.start_line, end_line=c.end_line,
                language=c.language, has_docstring=c.has_docstring,
            )
            for c in children
        ],
        outgoing_edges=_dedup(cross_out),
        incoming_edges=_dedup(cross_in),
    )
