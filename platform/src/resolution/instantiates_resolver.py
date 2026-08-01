"""Instantiates resolver — language-agnostic.

Detects object construction expressions (``ClassName(...)`` in Python,
``new ClassName(...)`` in TypeScript) and emits INSTANTIATES relationship
edges from the constructing entity (function / method / module) to the
class entity being instantiated.

SEMANTIC DISTINCTION FROM CALLS
=================================
The call resolver routes constructor calls to the ``__init__`` / ``constructor``
*method* entity and emits a CALLS edge.  This resolver emits a separate
INSTANTIATES edge pointing directly at the *class* entity itself.  The two
edges carry different information:

  CALLS   → "this code invokes that function/method"
  INSTANTIATES → "this code creates an instance of that class"

Both may exist for the same construction site; they are complementary, not
duplicates.

WHAT IS DETECTED
================
- Python: ``var = ClassName(...)`` or ``ClassName(...)`` as a standalone call
  where ``ClassName`` resolves to a known class entity in the same repository.
- TypeScript: ``new ClassName(...)`` where ``ClassName`` resolves to a known
  class entity.

OUT OF SCOPE (silently skipped)
================================
- External / third-party class instantiation (target not in entity set).
- Dynamic construction: ``cls_ref(...)`` where the class is determined at
  runtime through a variable.
- Re-exports and aliasing beyond what the symbol table resolves.

This file contains ZERO language-specific logic.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from src.extraction.models import Entity, Relationship
from src.languages.base import LanguageAdapter
from src.resolution.import_resolver import SymbolTable, FileSymbolTables

logger = logging.getLogger(__name__)


def resolve_instantiates(
    entities: list[Entity],
    symbol_tables: FileSymbolTables,
    repo_dir: Path,
    adapter_registry: dict[str, LanguageAdapter],
) -> list[Relationship]:
    """Detect object construction sites and emit INSTANTIATES edges.

    For every function / method / module entity, scans its source for
    ``ClassName(...)`` (Python) and ``new ClassName(...)`` (TypeScript /JS)
    patterns, resolves the class name to an in-repo entity ID, and emits a
    ``INSTANTIATES`` relationship edge.

    Args:
        entities:         All entities extracted from the repository.
        symbol_tables:    Per-file symbol tables built by the import resolver.
        repo_dir:         Absolute path to the repository root (unused here but
                          kept for interface consistency with other resolvers).
        adapter_registry: Language adapter map (unused here; detection is done
                          via source-text patterns rather than AST).

    Returns:
        A list of ``Relationship`` objects with ``type="INSTANTIATES"``.
    """
    relationships: list[Relationship] = []
    seen: set[tuple[str, str]] = set()  # (source_id, target_id) dedup

    entity_by_id: dict[str, Entity] = {e.id: e for e in entities}

    # Build name-in-file index: file_path → {entity_name → entity}
    name_in_file: dict[str, dict[str, Entity]] = {}
    for e in entities:
        name_in_file.setdefault(e.file_path, {})[e.name] = e

    # Class entities indexed by ID for quick type check
    class_entity_ids: set[str] = {
        e.id for e in entities if e.type in ("class", "interface")
    }

    # Children index for class entity lookup (needed to exclude child scope from module)
    children_of: dict[str, list[Entity]] = {}
    for e in entities:
        if e.parent_id:
            children_of.setdefault(e.parent_id, []).append(e)

    for entity in entities:
        if entity.type not in ("function", "method", "module"):
            continue

        source = entity.source or ""
        if not source.strip():
            continue

        file_symbols = symbol_tables.get(entity.file_path, {})
        local_names = name_in_file.get(entity.file_path, {})

        # For module entities, only scan top-level lines (exclude lines belonging
        # to child entity bodies so we don't double-attribute instantiations).
        if entity.type == "module":
            source = _strip_child_lines(source, entity, children_of)

        for class_name, line_no in _find_instantiation_sites(source, entity.start_line):
            target_id = _lookup_class(class_name, file_symbols, local_names, entity_by_id)
            if target_id is None:
                continue
            if target_id not in class_entity_ids:
                continue  # Resolved to a non-class entity (e.g. a function)
            if target_id == entity.id or target_id == entity.parent_id:
                continue  # Don't self-link

            edge = (entity.id, target_id)
            if edge not in seen:
                seen.add(edge)
                relationships.append(
                    Relationship(
                        source_id=entity.id,
                        target_id=target_id,
                        type="INSTANTIATES",
                        file_path=entity.file_path,
                        line=line_no,
                    )
                )
                logger.debug(
                    "INSTANTIATES: %s -> %s (line %d)",
                    entity.id,
                    target_id,
                    line_no,
                )

    return relationships


# ---------------------------------------------------------------------------
# Pattern matching helpers
# ---------------------------------------------------------------------------

# Matches: ClassName(  — Python direct call or TypeScript ClassName(
_PY_INSTANTIATION_RE = re.compile(r"\b([A-Z]\w*)\s*\(")

# Matches: new ClassName(  — TypeScript / JavaScript
_TS_NEW_RE = re.compile(r"\bnew\s+([A-Z]\w*)\s*\(")


def _find_instantiation_sites(
    source: str,
    entity_start_line: int,
) -> list[tuple[str, int]]:
    """Scan *source* and return (class_name, absolute_line_number) pairs.

    Combines Python-style ``ClassName(...)`` and TypeScript ``new ClassName(...)``
    patterns.  Uses absolute line numbers by offsetting from ``entity_start_line``.
    """
    results: list[tuple[str, int]] = []
    seen_names: set[str] = set()  # one edge per (source, class) pair is enough

    lines = source.splitlines()
    for rel_lineno, line in enumerate(lines, start=0):
        abs_lineno = entity_start_line + rel_lineno

        # TypeScript: new ClassName(
        for m in _TS_NEW_RE.finditer(line):
            name = m.group(1)
            if name not in seen_names:
                seen_names.add(name)
                results.append((name, abs_lineno))

        # Python / general: ClassName(  — but only PascalCase (upper first letter)
        # Skip names already captured by the new-keyword pattern on this line
        for m in _PY_INSTANTIATION_RE.finditer(line):
            name = m.group(1)
            if name not in seen_names:
                seen_names.add(name)
                results.append((name, abs_lineno))

    return results


def _strip_child_lines(
    source: str,
    module_entity: Entity,
    children_of: dict[str, list[Entity]],
) -> str:
    """Return *source* with lines belonging to direct child entity bodies blanked out.

    This prevents module-level instantiation detection from attributing
    constructor calls that live inside class / function bodies to the module.
    """
    children = children_of.get(module_entity.id, [])
    if not children:
        return source

    lines = source.splitlines(keepends=True)
    module_start = module_entity.start_line  # 1-based

    for child in children:
        # Convert to 0-based indices relative to module source
        child_rel_start = child.start_line - module_start
        child_rel_end = child.end_line - module_start
        for i in range(
            max(0, child_rel_start),
            min(len(lines), child_rel_end + 1),
        ):
            lines[i] = "\n"  # blank the line, keep line count intact

    return "".join(lines)


def _lookup_class(
    name: str,
    file_symbols: SymbolTable,
    name_in_file: dict[str, Entity],
    entity_by_id: dict[str, Entity],
) -> Optional[str]:
    """Resolve *name* to an entity ID, returning None for external symbols."""
    sym = file_symbols.get(name)
    if sym and not sym.startswith("external:"):
        return sym

    local = name_in_file.get(name)
    if local:
        return local.id

    return None
