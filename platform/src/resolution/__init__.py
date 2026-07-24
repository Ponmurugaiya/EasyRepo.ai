"""Relationship resolution package.

Orchestrates building of semantic relationship edges (CALLS, IMPORTS, INHERITS,
IMPLEMENTS) on top of the language-adapter interface.

SCOPE BOUNDARIES
================

IN SCOPE (will be resolved):
  - Direct, statically traceable function/method calls within the same repository.
  - Import statements resolved to entities that exist in the same repository.
  - Class inheritance (``class A(B)``, ``class A extends B``) where B is in-repo.
  - Interface/ABC implementation (``implements I``, ``class A(ABC)`` where A
    has abstractmethod parents) where I is in-repo.
  - Module-level calls attributed to the module entity (e.g. ``if __name__ == ...``).

OUT OF SCOPE (will be silently skipped, never guessed):
  - Dynamic dispatch, reflection, calls through untyped variables or callbacks
    that cannot be statically traced to a specific callee entity.
  - Calls to or imports from third-party libraries (numpy, express, etc.) —
    these produce no IMPORTS relationship; the symbol is marked "external".
  - Complex multiple-inheritance MRO edge cases.
  - Type-narrowing or generics that change the effective callee at runtime.
  - Cross-repository resolution.

Any unresolvable reference is debug-logged and skipped; no guessed edges are
emitted.  This ensures the graph contains only high-confidence relationships.

KEY PRINCIPLE: Every resolver in this package contains ZERO language-specific
logic.  Language-specific parsing is done by the LanguageAdapter implementations
in ``src.languages``.  Adding a new language (Go, Java, Rust, …) requires only
a new adapter; the resolvers need no changes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from src.extraction.models import Entity, Relationship
from src.languages.base import LanguageAdapter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def resolve_relationships(
    entities: list[Entity],
    repo_root: str,
    adapter_registry: dict[str, LanguageAdapter],
) -> list[Relationship]:
    """Build all semantic relationships for the extracted entity set.

    Args:
        entities:         All entities extracted by ``EntityExtractor``.
        repo_root:        Absolute path to the repository root.
        adapter_registry: Mapping of file extension -> ``LanguageAdapter``
                          (from ``src.languages.ADAPTER_REGISTRY``).

    Returns:
        A flat list of ``Relationship`` objects covering IMPORTS, CALLS,
        INHERITS, and IMPLEMENTS edges.
    """
    from src.resolution.import_resolver import build_symbol_tables
    from src.resolution.call_resolver import resolve_calls
    from src.resolution.inheritance_resolver import resolve_inheritance
    from src.resolution.implements_resolver import resolve_implements

    repo_dir = Path(repo_root).resolve()

    # 1. Build per-file symbol tables + emit IMPORTS edges
    symbol_tables, import_rels = build_symbol_tables(
        entities, repo_dir, adapter_registry
    )

    # 2. Resolve call expressions -> CALLS edges
    call_rels = resolve_calls(entities, symbol_tables, repo_dir, adapter_registry)

    # 3. Resolve inheritance -> INHERITS edges (delegates IMPLEMENTS cases)
    inherits_rels, implements_handoff = resolve_inheritance(
        entities, symbol_tables, repo_dir, adapter_registry
    )

    # 4. Resolve implements (explicit + handoff from inheritance resolver)
    implements_rels = resolve_implements(
        entities, symbol_tables, repo_dir, adapter_registry, implements_handoff
    )

    return import_rels + call_rels + inherits_rels + implements_rels


__all__ = ["resolve_relationships"]
