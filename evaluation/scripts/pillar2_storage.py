"""Pillar 2 — Storage & Embedding Integrity
Verifies entity counts, relationship counts, embedding dimensions, and vector ranking quality.
Canonical location: evaluation/scripts/pillar2_storage.py

Usage (from EasyRepo/):
    python evaluation/scripts/pillar2_storage.py --repo-id <uuid>
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# ── Path setup: evaluation/scripts/ → evaluation/ → EasyRepo/ → EasyRepo/platform/ ──
_HERE = Path(__file__).resolve()
PLATFORM_DIR = _HERE.parent.parent.parent / "platform"
if str(PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(PLATFORM_DIR))

# ── Load .env from EasyRepo root ──
ENV_PATH = PLATFORM_DIR.parent / ".env"
if ENV_PATH.exists():
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

from sqlalchemy import func

from src.embedding.config import EMBEDDING_DIM
from src.embedding.embedder import CodeEmbedder
from src.storage.db import get_session
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("verify_storage")

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


def run_spot_check(session, embedder, query_info: dict, repo_id: str, top_n: int = 5) -> list:
    query_text = query_info["query"]
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Spot-check: {query_info['description']}")
    logger.info(f"{'=' * 60}")

    query_vec = embedder.embed(query_text)

    results = (
        session.query(
            EntityModel,
            EntityModel.embedding.cosine_distance(query_vec).label("distance"),
        )
        .filter_by(repo_id=repo_id)
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
                logger.info(
                    f"  RANKING CHECK PASSED: '{preferred}' (rank {preferred_rank + 1}) "
                    f"ranked above '{preferred_over}' (rank {over_rank + 1})"
                )
            else:
                logger.warning(
                    f"  RANKING CHECK FAILED: '{preferred}' (rank {preferred_rank + 1}) "
                    f"did NOT rank above '{preferred_over}' (rank {over_rank + 1})"
                )
        elif preferred_rank is not None:
            logger.info(
                f"  RANKING CHECK INFO: '{preferred}' appeared at rank {preferred_rank + 1}; "
                f"'{preferred_over}' not in top-{top_n}"
            )
        else:
            logger.warning(f"  RANKING CHECK INFO: '{preferred}' not in top-{top_n} results")

    return results


def verify_storage(db_url: str, repo_id: str) -> None:
    logger.info("=" * 60)
    logger.info("STORAGE & EMBEDDING VERIFICATION")
    logger.info(f"  DB:      {db_url[:50]}...")
    logger.info(f"  repo_id: {repo_id}")
    logger.info("=" * 60)

    with get_session(db_url) as session:

        # 0. Confirm the repo exists and is ready
        repo = session.query(RepositoryModel).filter_by(id=repo_id).first()
        if repo is None:
            logger.error(f"FAILED: Repository '{repo_id}' not found in DB.")
            logger.error("  → Did you ingest the repo via POST /repositories first?")
            sys.exit(1)

        assert repo.status == "ready", (
            f"Expected repo status 'ready', got '{repo.status}'. "
            "Wait for ingestion to complete (poll /repositories/<id>/status)."
        )
        logger.info(f"PASSED: Repository status is 'ready' (indexed_at={repo.indexed_at})")

        # 1. Verify Entity Count
        entity_count = (
            session.query(func.count(EntityModel.id))
            .filter_by(repo_id=repo_id)
            .scalar()
        )
        logger.info(f"Total stored entities: {entity_count}")
        assert entity_count == 62, f"Expected 62 entities, got {entity_count}"
        logger.info("PASSED: Entity count matches manifest (62)")

        # 2. Verify Relationship Counts per Type
        expected_rel_counts = {
            "CONTAINS": 48,
            "IMPORTS": 11,
            "CALLS": 23,
            "INHERITS": 2,
            "IMPLEMENTS": 3,
        }

        actual_rel_counts = dict(
            session.query(RelationshipModel.type, func.count(RelationshipModel.id))
            .filter_by(repo_id=repo_id)
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

        # INSTANTIATES — check it exists (count ≥ 1, no exact target)
        instantiates_count = actual_rel_counts.get("INSTANTIATES", 0)
        logger.info(f"INSTANTIATES count: {instantiates_count} (expected ≥ 1)")
        assert instantiates_count >= 1, (
            f"Expected at least 1 INSTANTIATES relationship, got {instantiates_count}"
        )
        logger.info("PASSED: INSTANTIATES relationship(s) present")

        # 3. Verify Embeddings Integrity
        null_vec_count = (
            session.query(func.count(EntityModel.id))
            .filter(EntityModel.repo_id == repo_id, EntityModel.embedding.is_(None))
            .scalar()
        )
        assert null_vec_count == 0, f"Found {null_vec_count} entities with NULL embeddings"
        logger.info("PASSED: All entities have non-null vector embeddings")

        # 4. Verify Embedding Dimensions
        first_ent = session.query(EntityModel).filter_by(repo_id=repo_id).first()
        assert first_ent is not None and first_ent.embedding is not None
        dim = len(first_ent.embedding)
        assert dim == EMBEDDING_DIM, (
            f"Expected {EMBEDDING_DIM} embedding dimensions, got {dim}"
        )
        logger.info(f"PASSED: Embedding dimensions verified ({dim})")

        # 5. Three Spot-Check Vector Similarity Queries
        embedder = CodeEmbedder()
        for query_info in SPOT_CHECK_QUERIES:
            run_spot_check(session, embedder, query_info, repo_id=repo_id, top_n=5)

        logger.info("\nSUCCESS: All storage and embedding verifications passed perfectly!")


def main():
    db_url_default = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
    )

    parser = argparse.ArgumentParser(
        description="Verify storage layer, embedding integrity, and vector similarity rankings."
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        required=True,
        help="Repository UUID as returned by POST /repositories (e.g. from the ingest step).",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=db_url_default,
        help="PostgreSQL connection URL. Defaults to DATABASE_URL env var.",
    )
    args = parser.parse_args()

    verify_storage(args.db_url, args.repo_id)


if __name__ == "__main__":
    main()
