"""Repository ingestion pipeline for extracting, resolving, embedding, and persisting code context."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.embedding.embedder import CodeEmbedder, format_entity_for_embedding
from src.extraction.entity_extractor import EntityExtractor
from src.languages import ADAPTER_REGISTRY
from src.resolution import resolve_relationships
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel

logger = logging.getLogger(__name__)


def ingest_repository(
    repo_path_or_url: str,
    db_session: Session,
    repo_id: Optional[str] = None,
    repo_name: Optional[str] = None,
) -> RepositoryModel:
    """Extract, resolve, embed, and persist repository entities and relationships.

    Args:
        repo_path_or_url: Local path or repository URL.
        db_session: SQLAlchemy active session.
        repo_id: Optional custom repository ID. If None, derived from folder name.
        repo_name: Optional custom repository name. If None, derived from folder name.

    Returns:
        The updated RepositoryModel instance.
    """
    repo_path = Path(repo_path_or_url).resolve()
    if not repo_name:
        repo_name = repo_path.name
    if not repo_id:
        repo_id = repo_path.name.lower().replace(" ", "-")

    # 1. Initialize or update repository row with status = "indexing"
    repo = db_session.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        repo = RepositoryModel(
            id=repo_id,
            url_or_path=str(repo_path),
            name=repo_name,
            status="indexing",
        )
        db_session.add(repo)
    else:
        repo.url_or_path = str(repo_path)
        repo.name = repo_name
        repo.status = "indexing"
        # Clear previous entities and relationships if re-indexing
        db_session.execute(
            delete(RelationshipModel).where(RelationshipModel.repo_id == repo_id)
        )
        db_session.execute(
            delete(EntityModel).where(EntityModel.repo_id == repo_id)
        )

    db_session.commit()

    try:
        # 2. Run entity extraction and relationship resolution
        logger.info("Extracting entities from %s...", repo_path)
        extractor = EntityExtractor()
        extracted_ents, contains_rels = extractor.extract_repository(str(repo_path))

        logger.info("Resolving relationships for %d entities...", len(extracted_ents))
        resolved_rels = resolve_relationships(
            extracted_ents, str(repo_path), ADAPTER_REGISTRY
        )

        all_rels = contains_rels + resolved_rels

        # 3. Batch embed entities
        logger.info("Generating embeddings for %d entities...", len(extracted_ents))
        embedder = CodeEmbedder()
        texts = [format_entity_for_embedding(e) for e in extracted_ents]
        embeddings = embedder.embed_batch(texts)

        # 4. Bulk insert entities and relationships inside transaction
        # Sort entities by nesting level so parent entities exist before child FKs
        sorted_ents = sorted(extracted_ents, key=lambda e: (e.id.count("."), e.id))

        ent_id_to_vec = {
            ent.id: vec for ent, vec in zip(extracted_ents, embeddings)
        }
        known_entity_ids = {e.id for e in extracted_ents}

        db_entities: list[EntityModel] = []
        for ent in sorted_ents:
            db_ent = EntityModel(
                id=ent.id,
                repo_id=repo_id,
                type=ent.type,
                name=ent.name,
                file_path=ent.file_path,
                start_line=ent.start_line,
                end_line=ent.end_line,
                parent_id=(
                    ent.parent_id
                    if (ent.parent_id and ent.parent_id in known_entity_ids)
                    else None
                ),
                language=ent.language,
                has_docstring=ent.has_docstring,
                source=ent.source,
                embedding=ent_id_to_vec.get(ent.id),
            )
            db_entities.append(db_ent)

        db_session.add_all(db_entities)
        db_session.flush()

        db_rels: list[RelationshipModel] = []
        for rel in all_rels:
            is_internal_target = rel.target_id in known_entity_ids
            db_rel = RelationshipModel(
                repo_id=repo_id,
                source_id=rel.source_id,
                target_id=rel.target_id if is_internal_target else None,
                external_target_name=None if is_internal_target else rel.target_id,
                type=rel.type,
                file_path=rel.file_path,
                line=rel.line,
            )
            db_rels.append(db_rel)

        db_session.add_all(db_rels)

        # 5. Mark repository ready
        repo.status = "ready"
        repo.indexed_at = datetime.now(timezone.utc)
        db_session.commit()
        logger.info(
            "Successfully ingested %s (Entities: %d, Relationships: %d)",
            repo_name,
            len(db_entities),
            len(db_rels),
        )
        return repo

    except Exception as exc:
        db_session.rollback()
        logger.error("Ingestion failed for %s: %s", repo_name, exc, exc_info=True)
        repo = db_session.query(RepositoryModel).filter_by(id=repo_id).first()
        if repo:
            repo.status = "failed"
            db_session.commit()
        raise
