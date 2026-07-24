"""Re-embed entities that have zero CALLS relationships with an isolation annotation.

This enriches the embedding text with graph-derived isolation metadata so that
natural-language queries like "function with no dependencies" surface isolated
entities via vector search.

Usage:
    python scripts/enrich_isolated_embeddings.py [--repo-id REPO_ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
import os

# Allow running from platform/ root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

with open(r'P:\EasyRepo\.env') as f:
    for line in f:
        line = line.strip()
        if line and '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

from sqlalchemy import select, func
from src.storage.db import get_session
from src.storage.models import EntityModel, RelationshipModel
from src.embedding.embedder import CodeEmbedder, format_entity_for_embedding

DB_URL = "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo"


def _has_calls_edges(entity_id: str, session) -> bool:
    """Return True if the entity has any outgoing or incoming CALLS edges."""
    stmt = select(func.count(RelationshipModel.id)).where(
        RelationshipModel.type == "CALLS",
        (RelationshipModel.source_id == entity_id)
        | (RelationshipModel.target_id == entity_id),
    )
    count = session.scalar(stmt)
    return (count or 0) > 0


def _has_inherits_edges(entity_id: str, session) -> bool:
    """Return True if the entity has any INHERITS or IMPLEMENTS edges."""
    stmt = select(func.count(RelationshipModel.id)).where(
        RelationshipModel.type.in_(["INHERITS", "IMPLEMENTS"]),
        (RelationshipModel.source_id == entity_id)
        | (RelationshipModel.target_id == entity_id),
    )
    count = session.scalar(stmt)
    return (count or 0) > 0


def _children_have_calls_edges(entity_id: str, session) -> bool:
    """Return True if any CONTAINS child of entity_id has outgoing CALLS edges."""
    from sqlalchemy import select
    from src.storage.models import EntityModel as EM, RelationshipModel as RM
    child_ids_stmt = select(RM.target_id).where(
        RM.source_id == entity_id, RM.type == "CONTAINS"
    )
    child_ids = [row[0] for row in session.execute(child_ids_stmt).all() if row[0]]
    if not child_ids:
        return False
    calls_stmt = select(func.count(RM.id)).where(
        RM.source_id.in_(child_ids), RM.type == "CALLS"
    )
    count = session.scalar(calls_stmt)
    return (count or 0) > 0


def _all_children_isolated(entity_id: str, session) -> bool:
    """Return True if the entity has CONTAINS descendants and NONE of them have CALLS edges.

    Checks two levels deep (children and grandchildren) to handle the common
    module → class → method nesting pattern.  Only returns True for genuine
    orphan modules like formatting.py whose every descendant is dependency-free.
    """
    from sqlalchemy import select
    from src.storage.models import RelationshipModel as RM

    def _get_contains_children(parent_id: str) -> list[str]:
        stmt = select(RM.target_id).where(
            RM.source_id == parent_id, RM.type == "CONTAINS"
        )
        return [row[0] for row in session.execute(stmt).all() if row[0]]

    # Collect direct children
    direct_children = _get_contains_children(entity_id)
    if not direct_children:
        return False  # No children — can't classify as isolated module

    # Collect grandchildren
    all_descendants = list(direct_children)
    for child_id in direct_children:
        all_descendants.extend(_get_contains_children(child_id))

    # Check if ANY descendant has an outgoing CALLS edge
    calls_stmt = select(func.count(RM.id)).where(
        RM.source_id.in_(all_descendants), RM.type == "CALLS"
    )
    outgoing_calls = session.scalar(calls_stmt) or 0
    return outgoing_calls == 0


def enrich_isolated_embeddings(repo_id: str, dry_run: bool = False) -> None:
    embedder = CodeEmbedder()

    with get_session(DB_URL) as session:
        stmt = select(EntityModel).where(EntityModel.repo_id == repo_id)
        entities = session.scalars(stmt).all()

        to_update: list[EntityModel] = []
        for ent in entities:
            # --- function / method / class entities ---
            # These are directly annotatable if they have zero CALLS + INHERITS edges.
            # For class entities, also verify that their methods don't call other code.
            if ent.type in ("function", "method", "class"):
                if _has_calls_edges(ent.id, session) or _has_inherits_edges(ent.id, session):
                    continue
                if ent.type == "class" and _children_have_calls_edges(ent.id, session):
                    continue
                to_update.append(ent)
                continue

            # --- module entities ---
            # A module-level file entity can be annotated as isolated ONLY when
            # ALL of its CONTAINS children have zero outgoing CALLS edges.  This
            # covers true orphan files like formatting.py whose every function is
            # dependency-free.  Modules with any calling child are left unannotated.
            if ent.type == "module":
                if not _has_calls_edges(ent.id, session) and _all_children_isolated(ent.id, session):
                    to_update.append(ent)

        print(f"Found {len(to_update)} isolated entities (zero CALLS + INHERITS/IMPLEMENTS edges) out of {len(entities)} total.")

        for ent in to_update:
            base_text = format_entity_for_embedding(ent)
            # Append isolation annotation to improve retrieval for isolation queries
            isolation_annotation = (
                "\n\n[GRAPH METADATA] This entity is graph-isolated: "
                "it has zero outgoing CALLS relationships, zero incoming CALLS relationships, "
                "and zero INHERITS/IMPLEMENTS relationships. "
                "It has no dependencies on other code in this repository."
            )
            enriched_text = base_text + isolation_annotation

            print(f"  {'[DRY-RUN] ' if dry_run else ''}Re-embedding: {ent.id} ({ent.file_path}:{ent.start_line}-{ent.end_line})")
            if dry_run:
                print(f"    Enriched text preview: {enriched_text[:120].strip()}...")
            else:
                new_embedding = embedder.embed(enriched_text)
                ent.embedding = new_embedding

        if not dry_run:
            session.commit()
            print(f"\nCommitted {len(to_update)} re-embedded entities.")
        else:
            print(f"\n[DRY-RUN] Would re-embed {len(to_update)} entities.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-embed isolated entities with graph metadata annotation.")
    parser.add_argument("--repo-id", default="sample-repo", help="Repository ID to process")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be updated without writing")
    args = parser.parse_args()

    enrich_isolated_embeddings(repo_id=args.repo_id, dry_run=args.dry_run)
