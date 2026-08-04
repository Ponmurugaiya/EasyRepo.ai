"""File-level graph builder.

Aggregates entity-level relationships (CALLS, IMPORTS, INHERITS, INSTANTIATES)
up to the file level so the frontend can render a clean file-to-file graph.

The key transform:
  entity ``py.api.main.lifespan``  CALLS  ``py.storage.db.init_db``
      becomes →
  file  ``py.api.main``            CALLS  ``py.storage.db``
  
  with the original entity-pair preserved in ``FileEdge.connections``
  so the frontend can show them on hover/expand.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from src.graph.entry_points import EntryPointResult, detect_entry_points
from src.storage.models import EntityModel, RelationshipModel

# Edge type precedence for choosing the "dominant" type when multiple
# relationship types exist on the same file-to-file connection.
_TYPE_RANK: dict[str, int] = {
    "CALLS": 4,
    "INHERITS": 3,
    "IMPLEMENTS": 2,
    "INSTANTIATES": 2,
    "IMPORTS": 1,
    "CONTAINS": 0,  # always intra-file, filtered out before reaching here
}


@dataclass
class EntityConnection:
    """One resolved entity-level connection behind a file edge."""
    from_entity_id: str
    from_entity_name: str
    to_entity_id: str
    to_entity_name: str
    rel_type: str
    line: int


@dataclass
class FileNode:
    """A file (module entity) as a graph node."""
    id: str                  # module entity id, e.g. "py.api.main"
    file_path: str           # "src/api/main.py"
    name: str                # "main.py"
    language: str
    entity_count: int = 0
    is_entry: bool = False
    entry_score: int = 0


@dataclass
class FileEdge:
    """An aggregated cross-file relationship."""
    source_file_id: str
    target_file_id: str
    rel_types: list[str] = field(default_factory=list)
    dominant_type: str = "CALLS"
    connections: list[EntityConnection] = field(default_factory=list)


@dataclass
class FileGraph:
    """Complete file-level graph for a repository."""
    nodes: dict[str, FileNode]        # keyed by module entity id
    edges: list[FileEdge]
    entry_points: list[EntryPointResult]

    @property
    def root(self) -> str | None:
        """Return the highest-scored entry point entity id."""
        return self.entry_points[0].entity_id if self.entry_points else None


def build_file_graph(repo_id: str, db: Session) -> FileGraph:
    """Build the complete file-level graph for *repo_id*.

    Steps:
    1. Fetch all module entities  → file nodes
    2. Fetch all entities         → entity_id → file_path lookup
    3. Fetch all relationships    → aggregate cross-file edges
    4. Score entry points
    5. Count child entities per file
    """
    # ── 1. File nodes from module entities ─────────────────────────────────
    modules: list[EntityModel] = (
        db.query(EntityModel)
        .filter_by(repo_id=repo_id, type="module")
        .all()
    )

    nodes: dict[str, FileNode] = {
        m.id: FileNode(
            id=m.id,
            file_path=m.file_path,
            name=Path(m.file_path).name,
            language=m.language,
        )
        for m in modules
    }

    if not nodes:
        return FileGraph(nodes={}, edges=[], entry_points=[])

    # ── 2. Entity → file_path + name lookup ────────────────────────────────
    all_entities: list[EntityModel] = (
        db.query(
            EntityModel.id,
            EntityModel.name,
            EntityModel.file_path,
            EntityModel.type,
        )
        .filter_by(repo_id=repo_id)
        .all()
    )

    entity_file: dict[str, str] = {}   # entity_id → module entity id
    entity_name: dict[str, str] = {}   # entity_id → entity name

    # Build file_path → module_id reverse map
    file_to_module: dict[str, str] = {m.file_path: m.id for m in modules}

    for ent in all_entities:
        module_id = file_to_module.get(ent.file_path)
        if module_id:
            entity_file[ent.id] = module_id
            entity_name[ent.id] = ent.name

        # Count non-module children per file
        if ent.type != "module" and module_id and module_id in nodes:
            nodes[module_id].entity_count += 1

    # ── 3. Fetch relationships and aggregate to file level ─────────────────
    rels: list[RelationshipModel] = (
        db.query(RelationshipModel)
        .filter(
            RelationshipModel.repo_id == repo_id,
            RelationshipModel.type.in_(
                ["CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS", "INSTANTIATES"]
            ),
            RelationshipModel.target_id.isnot(None),
        )
        .all()
    )

    # Group connections by (source_file_id, target_file_id)
    # Key → list of EntityConnection
    edge_map: dict[tuple[str, str], list[EntityConnection]] = defaultdict(list)
    # Also track which rel_types appear on each file pair
    edge_types: dict[tuple[str, str], set[str]] = defaultdict(set)

    for rel in rels:
        src_file = entity_file.get(rel.source_id)
        tgt_file = entity_file.get(rel.target_id)

        # Skip intra-file edges and edges where we can't resolve files
        if not src_file or not tgt_file or src_file == tgt_file:
            continue

        # Skip if source or target file not in our node set
        if src_file not in nodes or tgt_file not in nodes:
            continue

        key = (src_file, tgt_file)
        edge_types[key].add(rel.type)
        edge_map[key].append(
            EntityConnection(
                from_entity_id=rel.source_id,
                from_entity_name=entity_name.get(rel.source_id, rel.source_id.split(".")[-1]),
                to_entity_id=rel.target_id,
                to_entity_name=entity_name.get(rel.target_id, rel.target_id.split(".")[-1]),
                rel_type=rel.type,
                line=rel.line,
            )
        )

    # Build FileEdge objects, deduplicating connections
    edges: list[FileEdge] = []
    for (src_file, tgt_file), connections in edge_map.items():
        types = sorted(
            edge_types[(src_file, tgt_file)],
            key=lambda t: _TYPE_RANK.get(t, 0),
            reverse=True,
        )
        dominant = types[0] if types else "CALLS"

        # Deduplicate connections by (from, to, type)
        seen_conns: set[tuple[str, str, str]] = set()
        deduped: list[EntityConnection] = []
        for conn in connections:
            key2 = (conn.from_entity_id, conn.to_entity_id, conn.rel_type)
            if key2 not in seen_conns:
                seen_conns.add(key2)
                deduped.append(conn)

        edges.append(
            FileEdge(
                source_file_id=src_file,
                target_file_id=tgt_file,
                rel_types=types,
                dominant_type=dominant,
                connections=deduped,
            )
        )

    # ── 4. Entry point scoring ──────────────────────────────────────────────
    entry_points = detect_entry_points(repo_id, db)

    for ep in entry_points:
        if ep.entity_id in nodes:
            nodes[ep.entity_id].is_entry = True
            nodes[ep.entity_id].entry_score = ep.score

    return FileGraph(
        nodes=nodes,
        edges=edges,
        entry_points=entry_points,
    )
