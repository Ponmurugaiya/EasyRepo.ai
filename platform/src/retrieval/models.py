"""Dataclasses for the retrieval pipeline.

Hierarchy
---------
vector_search  →  list[RetrievalResult]
                        ↓
relationship_expander  →  list[ExpandedContext]
                                ↓
context_builder  →  FinalContext (rendered_text ready for prompting)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.models import EntityModel


@dataclass
class RetrievalResult:
    """A single entity returned by vector similarity search.

    Attributes
    ----------
    entity_id:
        The entity's primary key (e.g. ``py.services.auth_service.AuthService.validate``).
    entity:
        Full ORM row from the ``entities`` table, including ``source`` and ``embedding``.
    score:
        Cosine similarity in [0, 1]; higher is more similar to the query.
    rank:
        1-based rank (1 = most similar).
    """

    entity_id: str
    entity: "EntityModel"
    score: float
    rank: int


@dataclass
class CalledEntity:
    """An entity reachable via outgoing CALLS edges from a core entity.

    Attributes
    ----------
    entity:
        The callee ORM row.
    depth:
        1 = directly called by the core entity.
        2 = called by a depth-1 callee (transitive).
    called_via:
        entity_id of the intermediate entity that directly calls this one.
        For depth-1 entities this equals the core entity's id.
    """

    entity: "EntityModel"
    depth: int
    called_via: str


@dataclass
class ExpandedContext:
    """Graph-expanded context for one core retrieved entity.

    Produced by ``relationship_expander.expand()``.

    Attributes
    ----------
    core:
        The original retrieval result this context was expanded from.
    parent_entity:
        The CONTAINS-parent entity (class or module), if expansion rule 1 triggered.
        ``None`` if the parent was not pulled in or was already a core result.
    parent_was_also_retrieved:
        True when the parent entity was independently retrieved by vector search
        (so it appears in another ExpandedContext.core, not duplicated here).
    called_entities:
        Outgoing CALLS neighbours up to ``CALLS_OUTGOING_DEPTH`` (default 2).
        Ordered by (depth ASC, entity_id ASC).
    caller_entities:
        Incoming CALLS neighbours (entities that call the core entity) up to
        ``CALLS_INCOMING_DEPTH`` (default 1).
    inheritance_entities:
        INHERITS / IMPLEMENTS targets up to ``INHERITANCE_DEPTH`` (default 2).
    """

    core: RetrievalResult
    parent_entity: "EntityModel | None" = None
    parent_was_also_retrieved: bool = False
    called_entities: list[CalledEntity] = field(default_factory=list)
    caller_entities: list["EntityModel"] = field(default_factory=list)
    inheritance_entities: list["EntityModel"] = field(default_factory=list)


@dataclass
class FinalContext:
    """Assembled, prompt-ready context object.

    Produced by ``context_builder.build_context()``.

    Attributes
    ----------
    query:
        The original natural-language query string.
    repo_id:
        Repository identifier used during retrieval.
    expanded_contexts:
        The full list of ``ExpandedContext`` objects (before budget truncation).
    rendered_text:
        The final prompt-ready string — entity snippets + execution traces.
    total_tokens_est:
        Rough token estimate: ``len(rendered_text) // 4``.
    truncated:
        True if some lower-priority expansion context was dropped to fit the budget.
    """

    query: str
    repo_id: str
    expanded_contexts: list[ExpandedContext]
    rendered_text: str
    total_tokens_est: int
    truncated: bool
