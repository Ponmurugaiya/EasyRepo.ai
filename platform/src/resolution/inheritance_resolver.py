"""Inheritance resolver — language-agnostic.

Consumes base-class names from adapter.get_inheritance_bases() and emits
INHERITS relationship edges.

If a resolved base class has ``is_interface_like == True`` (i.e. it is typed
as an interface / ABC), the edge is handed off to the implements resolver
instead of being emitted as INHERITS — preventing double-counting.

This file contains ZERO language-specific logic.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import tree_sitter

from src.extraction.models import Entity, Relationship
from src.languages.base import LanguageAdapter
from src.resolution.import_resolver import SymbolTable, FileSymbolTables

logger = logging.getLogger(__name__)

# Handed off to implements_resolver: list of (class_entity, interface_entity)
InheritHandoff = list[tuple[Entity, Entity]]


def resolve_inheritance(
    entities: list[Entity],
    symbol_tables: FileSymbolTables,
    repo_dir: Path,
    adapter_registry: dict[str, LanguageAdapter],
) -> tuple[list[Relationship], InheritHandoff]:
    """Resolve class inheritance, emit INHERITS edges, and hand off interface cases.

    Returns:
        (inherits_relationships, implements_handoff)
        where *implements_handoff* is passed to ``implements_resolver``.
    """
    inherits_rels: list[Relationship] = []
    handoff: InheritHandoff = []

    entity_by_id: dict[str, Entity] = {e.id: e for e in entities}
    name_in_file: dict[str, dict[str, Entity]] = {}
    for e in entities:
        name_in_file.setdefault(e.file_path, {})[e.name] = e

    for class_entity in entities:
        if class_entity.type not in ("class", "interface"):
            continue

        suffix = Path(class_entity.file_path).suffix
        adapter = adapter_registry.get(suffix)
        if adapter is None:
            continue

        full_path = repo_dir / class_entity.file_path
        if not full_path.exists():
            continue

        try:
            source_bytes = full_path.read_bytes()
        except OSError:
            continue

        import tree_sitter as _ts
        lang = adapter.get_tree_sitter_language()
        parser = _ts.Parser(lang)
        tree = parser.parse(source_bytes)
        if not tree:
            continue

        class_node = _find_node_by_lines(
            tree.root_node, class_entity.start_line, class_entity.end_line
        )
        if class_node is None:
            continue

        base_names = adapter.get_inheritance_bases(class_node, source_bytes)
        file_symbols = symbol_tables.get(class_entity.file_path, {})

        for base_name in base_names:
            target_id = _resolve_name(
                base_name, file_symbols, name_in_file.get(class_entity.file_path, {})
            )
            if not target_id or target_id.startswith("external:"):
                logger.debug(
                    "Unresolvable base class %s for %s", base_name, class_entity.id
                )
                continue

            target_ent = entity_by_id.get(target_id)
            if not target_ent:
                continue

            # Decide: INHERITS or IMPLEMENTS?
            if adapter.is_interface_like(target_ent.type, target_ent.file_path):
                # Hand off to implements_resolver
                handoff.append((class_entity, target_ent))
            else:
                inherits_rels.append(
                    Relationship(
                        source_id=class_entity.id,
                        target_id=target_id,
                        type="INHERITS",
                        file_path=class_entity.file_path,
                        line=class_entity.start_line,
                    )
                )

    return inherits_rels, handoff


def _resolve_name(
    name: str,
    file_symbols: SymbolTable,
    name_in_file: dict[str, Entity],
) -> Optional[str]:
    sym = file_symbols.get(name)
    if sym and not sym.startswith("external:"):
        return sym
    local = name_in_file.get(name)
    if local:
        return local.id
    return None


def _find_node_by_lines(
    root: tree_sitter.Node, start_line: int, end_line: int
) -> Optional[tree_sitter.Node]:
    """Find the most-specific class/interface AST node matching the line range."""
    best: Optional[tree_sitter.Node] = None

    def visit(node: tree_sitter.Node) -> None:
        nonlocal best
        node_start = node.start_point[0] + 1
        node_end = node.end_point[0] + 1
        if node_start <= start_line and node_end >= end_line:
            if node.type in (
                "class_definition",
                "class_declaration",
                "decorated_definition",
                "interface_declaration",
            ):
                if best is None or (node_end - node_start) <= (
                    best.end_point[0] - best.start_point[0]
                ):
                    best = node
        for child in node.children:
            visit(child)

    visit(root)
    return best
