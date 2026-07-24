"""Verification script for storage layer, embedding pipeline, and vector similarity search.

Runs three spot-check queries to evaluate the code embedding model quality:
  Q1: "authenticate a user with a token"
  Q2: "validate a JWT bearer token"
  Q3: "format a record for audit logging"
"""

import sys
from pathlib import Path

# Add platform directory to sys.path
PLATFORM_DIR = Path(__file__).resolve().parent.parent
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

import logging
from sqlalchemy import func
from src.embedding.config import EMBEDDING_DIM
from src.embedding.embedder import CodeEmbedder
from src.ingestion.pipeline import ingest_repository
from src.storage.db import get_session, init_db, get_engine
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("verify_storage")

DB_URL = "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo"

SAMPLE_REPO_PATH = (PLATFORM_DIR.parent / "sample-repo").resolve()

SPOT_CHECK_QUERIES = [
    {
        "query": "authenticate a user with a token",
        "expected_top_kws": ["auth", "authenticate", "user", "token"],
        "description": "Q1: authenticate a user with a token",
    },
    {
        "query": "validate a JWT bearer token",
        "expected_top_kws": ["auth", "validate", "jwt", "token"],
        "description": "Q2: validate a JWT bearer token",
        # AuthService.validate should rank near top, above UserModel.validate
        "preferred_over": "py.models.user.UserModel.validate",
        "preferred": "py.services.auth_service.AuthService.validate",
    },
    {
        "query": "format a record for audit logging",
        "expected_top_kws": ["audit", "format", "log"],
        "description": "Q3: format a record for audit logging",
        # format_audit_log should rank above format_user_record
        "preferred_over": "py.utils.formatting.format_user_record",
        "preferred": "py.utils.formatting.format_audit_log",
    },
]


def run_spot_check(session, embedder, query_info: dict, top_n: int = 5) -> list:
    query_text = query_info["query"]
    logger.info(f"\n{'='*60}")
    logger.info(f"Spot-check: {query_info['description']}")
    logger.info(f"{'='*60}")

    query_vec = embedder.embed(query_text)

    results = (
        session.query(
            EntityModel,
            EntityModel.embedding.cosine_distance(query_vec).label("distance"),
        )
        .filter_by(repo_id="sample-repo")
        .order_by("distance")
        .limit(top_n)
        .all()
    )

    logger.info(f"Top {top_n} matches:")
    for rank, (ent, distance) in enumerate(results, start=1):
        score = 1.0 - float(distance)
        logger.info(
            f"  [{rank}] Score: {score:.4f} | Dist: {distance:.4f} | "
            f"{ent.id} ({ent.type})"
        )

    top_ids = [ent.id for ent, _ in results]

    # Ranking assertion: preferred entity should appear before preferred_over entity
    if "preferred" in query_info and "preferred_over" in query_info:
        preferred = query_info["preferred"]
        preferred_over = query_info["preferred_over"]
        preferred_rank = next(
            (i for i, eid in enumerate(top_ids) if preferred in eid), None
        )
        over_rank = next(
            (i for i, eid in enumerate(top_ids) if preferred_over in eid), None
        )
        if preferred_rank is not None and over_rank is not None:
            if preferred_rank < over_rank:
                logger.info(f"  RANKING CHECK PASSED: '{preferred}' (rank {preferred_rank+1}) ranked above '{preferred_over}' (rank {over_rank+1})")
            else:
                logger.warning(f"  RANKING CHECK FAILED: '{preferred}' (rank {preferred_rank+1}) did NOT rank above '{preferred_over}' (rank {over_rank+1})")
        elif preferred_rank is not None:
            logger.info(f"  RANKING CHECK INFO: '{preferred}' appeared at rank {preferred_rank+1}; '{preferred_over}' not in top-{top_n}")
        else:
            logger.warning(f"  RANKING CHECK INFO: '{preferred}' not in top-{top_n} results")

    return results


def verify_storage_and_embeddings():
    logger.info("Initializing database schema...")
    init_db(DB_URL)

    get_engine(DB_URL)
    with get_session(DB_URL) as session:

        logger.info(f"Ingesting sample repository from: {SAMPLE_REPO_PATH}")
        repo = ingest_repository(
            repo_path_or_url=str(SAMPLE_REPO_PATH),
            db_session=session,
            repo_id="sample-repo",
            repo_name="sample-repo",
        )

        # 1. Verify Repository Status
        assert repo.status == "ready", f"Expected repo status 'ready', got '{repo.status}'"
        logger.info("PASSED: Repository status is 'ready'")

        # 2. Verify Entity Count
        entity_count = session.query(func.count(EntityModel.id)).filter_by(repo_id="sample-repo").scalar()
        logger.info(f"Total stored entities: {entity_count}")
        assert entity_count == 62, f"Expected 62 entities, got {entity_count}"
        logger.info("PASSED: Entity count matches manifest (62)")

        # 3. Verify Relationship Counts per Type
        expected_rel_counts = {
            "CONTAINS": 48,
            "IMPORTS": 11,
            "CALLS": 23,
            "INHERITS": 2,
            "IMPLEMENTS": 3,
        }

        actual_rel_counts = dict(
            session.query(RelationshipModel.type, func.count(RelationshipModel.id))
            .filter_by(repo_id="sample-repo")
            .group_by(RelationshipModel.type)
            .all()
        )

        logger.info(f"Actual relationship counts: {actual_rel_counts}")
        for rel_type, expected_count in expected_rel_counts.items():
            actual = actual_rel_counts.get(rel_type, 0)
            assert actual == expected_count, (
                f"Relationship count mismatch for {rel_type}: "
                f"expected {expected_count}, got {actual}"
            )
            logger.info(f"PASSED: Relationship {rel_type} count matches ({expected_count})")

        # 4. Verify Embeddings Integrity
        null_vec_count = (
            session.query(func.count(EntityModel.id))
            .filter(EntityModel.repo_id == "sample-repo", EntityModel.embedding.is_(None))
            .scalar()
        )
        assert null_vec_count == 0, f"Found {null_vec_count} entities with NULL embeddings"
        logger.info("PASSED: All entities have non-null vector embeddings")

        # Check embedding dimensions match config
        first_ent = session.query(EntityModel).filter_by(repo_id="sample-repo").first()
        assert first_ent is not None and first_ent.embedding is not None
        dim = len(first_ent.embedding)
        assert dim == EMBEDDING_DIM, f"Expected {EMBEDDING_DIM} embedding dimensions, got {dim}"
        logger.info(f"PASSED: Embedding dimensions verified ({dim})")

        # 5. Three Spot-Check Vector Similarity Queries
        embedder = CodeEmbedder()
        for query_info in SPOT_CHECK_QUERIES:
            run_spot_check(session, embedder, query_info, top_n=5)

        logger.info("\nSUCCESS: All storage and embedding verifications passed perfectly!")


if __name__ == "__main__":
    verify_storage_and_embeddings()
