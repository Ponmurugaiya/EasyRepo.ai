"""Implements resolver — language-agnostic.

Consumes:
  1. ``adapter.get_implements_bases()`` (TypeScript ``implements`` clause)
  2. Handoff from the inheritance resolver (Python ABC/Protocol bases that
     resolve to interface-like entities)

Emits IMPLEMENTS relationship edges.

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
from src.resolution.inheritance_resolver import InheritHandoff, _find_node_by_lines

logger = logging.getLogger(__name__)


def resolve_implements(
    entities: list[Entity],
    symbol_tables: FileSymbolTables,
    repo_dir: Path,
    adapter_registry: dict[str, LanguageAdapter],
    handoff: InheritHandoff,
) -> list[Relationship]:
    """Emit IMPLEMENTS edges.

    Sources:
    - Explicit ``implements`` clause (TypeScript) via adapter.get_implements_bases()
    - ABC/Protocol bases handed off from the inheritance resolver (Python)
    """
    implements_rels: list[Relationship] = []
    seen: set[tuple[str, str]] = set()

    entity_by_id: dict[str, Entity] = {e.id: e for e in entities}
    name_in_file: dict[str, dict[str, Entity]] = {}
    for e in entities:
        name_in_file.setdefault(e.file_path, {})[e.name] = e

    # ------------------------------------------------------------------
    # A. Handoff from inheritance resolver (Python ABC/interface cases)
    # ------------------------------------------------------------------
    for class_entity, interface_entity in handoff:
        key = (class_entity.id, interface_entity.id)
        if key not in seen:
            seen.add(key)
            implements_rels.append(
                Relationship(
                    source_id=class_entity.id,
                    target_id=interface_entity.id,
                    type="IMPLEMENTS",
                    file_path=class_entity.file_path,
                    line=class_entity.start_line,
                )
            )

    # ------------------------------------------------------------------
    # B. Explicit implements clause (TypeScript + any future language)
    # ------------------------------------------------------------------
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

        impl_names = adapter.get_implements_bases(class_node, source_bytes)
        if not impl_names:
            continue

        file_symbols = symbol_tables.get(class_entity.file_path, {})

        for impl_name in impl_names:
            target_id = _resolve_name(
                impl_name, file_symbols, name_in_file.get(class_entity.file_path, {})
            )
            if not target_id or target_id.startswith("external:"):
                logger.debug(
                    "Unresolvable implements target %s for %s",
                    impl_name,
                    class_entity.id,
                )
                continue

            if target_id not in entity_by_id:
                continue

            key = (class_entity.id, target_id)
            if key not in seen:
                seen.add(key)
                implements_rels.append(
                    Relationship(
                        source_id=class_entity.id,
                        target_id=target_id,
                        type="IMPLEMENTS",
                        file_path=class_entity.file_path,
                        line=class_entity.start_line,
                    )
                )

    return implements_rels


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
