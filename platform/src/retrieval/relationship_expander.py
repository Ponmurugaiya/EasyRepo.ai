"""Relationship expander module for graph-based context expansion.

Implements expansion rules:
1. PARENT EXPANSION (CONTAINS, upward): Pull parent class/module if 2+ retrieved
   entities share it OR if entity is a method with self references needing class context.
2. EXECUTION PATH RECONSTRUCTION (CALLS, both directions, bounded depth):
   Walk outgoing CALLS edges up to max depth (default 2), incoming CALLS up to max depth 1.
3. INHERITANCE CONTEXT: Walk INHERITS/IMPLEMENTS edges (depth 2 to support multi-level inheritance).
4. DEDUPLICATION: Avoid duplicating entities across core retrieval and expansion sets.
"""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.storage.models import EntityModel, RelationshipModel
from src.retrieval.models import CalledEntity, ExpandedContext, RetrievalResult

# Bounded depth configurations
CALLS_OUTGOING_DEPTH = 3
CALLS_INCOMING_DEPTH = 1
INHERITANCE_DEPTH = 2
SHARED_PARENT_THRESHOLD = 2



def _should_expand_parent(
    entity: EntityModel,
    parent_counts: Dict[str, int],
) -> bool:
    """Heuristic to decide if a parent entity should be pulled into context.

    Rule:
    1. If 2+ retrieved entities share the same parent_id.
    2. OR if entity is a 'method' and its source code makes multiple `self.` calls/accesses
       (e.g., self.something), indicating surrounding class context would materially help.
    """
    if not entity.parent_id:
        return False

    # Condition 1: Shared parent threshold
    if parent_counts.get(entity.parent_id, 0) >= SHARED_PARENT_THRESHOLD:
        return True

    # Condition 2: Method referencing self
    if entity.type == "method" and entity.source:
        self_refs = len(re.findall(r"\bself\.\w+", entity.source))
        if self_refs >= 2:
            return True

    return False


def _get_entity_by_id(entity_id: str, db_session: Session) -> EntityModel | None:
    return db_session.scalars(
        select(EntityModel).where(EntityModel.id == entity_id)
    ).first()


def _get_relationships(
    repo_id: str,
    source_ids: List[str] | None = None,
    target_ids: List[str] | None = None,
    types: List[str] | None = None,
    db_session: Session = None, # type: ignore
) -> List[RelationshipModel]:
    stmt = select(RelationshipModel).where(RelationshipModel.repo_id == repo_id)
    if source_ids:
        stmt = stmt.where(RelationshipModel.source_id.in_(source_ids))
    if target_ids:
        stmt = stmt.where(RelationshipModel.target_id.in_(target_ids))
    if types:
        stmt = stmt.where(RelationshipModel.type.in_(types))
    return list(db_session.scalars(stmt).all())


def expand(
    retrieved_results: List[RetrievalResult],
    repo_id: str,
    db_session: Session,
    calls_outgoing_depth: int = CALLS_OUTGOING_DEPTH,
    calls_incoming_depth: int = CALLS_INCOMING_DEPTH,
    inheritance_depth: int = INHERITANCE_DEPTH,
) -> List[ExpandedContext]:
    """Expand context for each top-k retrieved result using graph relationships.

    Parameters
    ----------
    retrieved_results:
        Top-k vector search results.
    repo_id:
        Repository ID.
    db_session:
        Active DB session.
    calls_outgoing_depth:
        Max depth for outgoing CALLS edges (default 2).
    calls_incoming_depth:
        Max depth for incoming CALLS edges (default 1).
    inheritance_depth:
        Max depth for INHERITS / IMPLEMENTS edges (default 2).

    Returns
    -------
    List[ExpandedContext]
        Expanded context structure per core entity.
    """
    if not retrieved_results:
        return []

    # Map core entity IDs for quick lookup and deduplication
    core_entity_ids = {r.entity_id for r in retrieved_results}
    
    # Count occurrences of parent_ids among core retrieved entities
    parent_counts: Dict[str, int] = {}
    for res in retrieved_results:
        p_id = res.entity.parent_id
        if p_id:
            parent_counts[p_id] = parent_counts.get(p_id, 0) + 1

    expanded_contexts: List[ExpandedContext] = []
    
    # Track overall seen entity IDs per core expansion to avoid duplication in single branch,
    # plus entity cache to minimize DB queries.
    entity_cache: Dict[str, EntityModel] = {res.entity_id: res.entity for res in retrieved_results}

    def fetch_entity(eid: str) -> EntityModel | None:
        if eid not in entity_cache:
            ent = _get_entity_by_id(eid, db_session)
            if ent:
                entity_cache[eid] = ent
        return entity_cache.get(eid)

    for res in retrieved_results:
        core_ent = res.entity
        seen_in_this_expansion: Set[str] = {core_ent.id}

        # 1. PARENT EXPANSION (CONTAINS)
        parent_entity: EntityModel | None = None
        parent_was_retrieved = False

        if core_ent.parent_id:
            if core_ent.parent_id in core_entity_ids:
                parent_was_retrieved = True
            elif _should_expand_parent(core_ent, parent_counts):
                # Verify that a real CONTAINS relationship exists in DB from parent_id -> core_ent.id
                contains_rel = db_session.scalars(
                    select(RelationshipModel).where(
                        RelationshipModel.repo_id == repo_id,
                        RelationshipModel.source_id == core_ent.parent_id,
                        RelationshipModel.target_id == core_ent.id,
                        RelationshipModel.type == "CONTAINS",
                    )
                ).first()

                if contains_rel:
                    parent_ent = fetch_entity(core_ent.parent_id)
                    if parent_ent:
                        parent_entity = parent_ent
                        seen_in_this_expansion.add(parent_ent.id)


        # 2. OUTGOING CALLS (BFS up to calls_outgoing_depth)
        called_entities: List[CalledEntity] = []
        # Queue item: (entity_id, current_depth, called_via)
        queue: List[Tuple[str, int, str]] = [(core_ent.id, 0, core_ent.id)]
        
        while queue:
            curr_id, curr_depth, via_id = queue.pop(0)
            if curr_depth >= calls_outgoing_depth:
                continue

            rels = _get_relationships(
                repo_id=repo_id,
                source_ids=[curr_id],
                types=["CALLS"],
                db_session=db_session,
            )

            for rel in rels:
                target_id = rel.target_id
                if target_id and target_id not in seen_in_this_expansion:
                    target_ent = fetch_entity(target_id)
                    if target_ent:
                        seen_in_this_expansion.add(target_id)
                        next_depth = curr_depth + 1
                        called_entities.append(
                            CalledEntity(
                                entity=target_ent,
                                depth=next_depth,
                                called_via=via_id,
                            )
                        )
                        queue.append((target_id, next_depth, target_id))

        # 3. INCOMING CALLS (max depth 1)
        caller_entities: List[EntityModel] = []
        if calls_incoming_depth > 0:
            incoming_rels = _get_relationships(
                repo_id=repo_id,
                target_ids=[core_ent.id],
                types=["CALLS"],
                db_session=db_session,
            )
            for rel in incoming_rels:
                src_id = rel.source_id
                if src_id not in seen_in_this_expansion:
                    src_ent = fetch_entity(src_id)
                    if src_ent:
                        seen_in_this_expansion.add(src_id)
                        caller_entities.append(src_ent)

        # 4. INHERITANCE CONTEXT (INHERITS / IMPLEMENTS up to inheritance_depth)
        inheritance_entities: List[EntityModel] = []
        inh_queue: List[Tuple[str, int]] = [(core_ent.id, 0)]
        
        while inh_queue:
            curr_id, curr_depth = inh_queue.pop(0)
            if curr_depth >= inheritance_depth:
                continue

            inh_rels = _get_relationships(
                repo_id=repo_id,
                source_ids=[curr_id],
                types=["INHERITS", "IMPLEMENTS"],
                db_session=db_session,
            )

            for rel in inh_rels:
                target_id = rel.target_id
                if target_id and target_id not in seen_in_this_expansion:
                    target_ent = fetch_entity(target_id)
                    if target_ent:
                        seen_in_this_expansion.add(target_id)
                        inheritance_entities.append(target_ent)
                        inh_queue.append((target_id, curr_depth + 1))

        expanded_contexts.append(
            ExpandedContext(
                core=res,
                parent_entity=parent_entity,
                parent_was_also_retrieved=parent_was_retrieved,
                called_entities=called_entities,
                caller_entities=caller_entities,
                inheritance_entities=inheritance_entities,
            )
        )

    return expanded_contexts
