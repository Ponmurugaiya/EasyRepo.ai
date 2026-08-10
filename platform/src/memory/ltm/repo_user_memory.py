"""Repo-User Memory — codebase facts per user (LTM tier 6).

Stores hard facts about the codebase itself, discovered or confirmed through
conversations with a specific user. These are about the *repository*, not
about the user.

Examples:
  - "Auth uses JWT with 24h expiry"
  - "Race condition in UserService.create()"
  - "GraphQL schema was migrated in Q4 2025"

Scope:   (user_id, repo_id)
Expires: should be treated as stale after a repo re-index (same rule as
         session_knowledge), though this is not yet enforced automatically.
Written: by summarize_after_turn() after each conversation exchange

Public API
----------
load_repo_user_memory(user_id, repo_id, db)             -> list[dict]
upsert_repo_user_memory(user_id, repo_id, facts, db)    -> None
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.storage.models import RepoUserMemoryModel

logger = logging.getLogger(__name__)


def load_repo_user_memory(
    user_id: str, repo_id: str, db: Session
) -> list[dict]:
    """Return all repo-fact memory entries for this user + repo combination.

    Returns
    -------
    list[dict]
        Each dict has ``category`` and ``fact`` keys.
    """
    try:
        rows = (
            db.query(RepoUserMemoryModel)
            .filter_by(user_id=user_id, repo_id=repo_id)
            .order_by(RepoUserMemoryModel.created_at.asc())
            .all()
        )
        return [{"category": r.category, "fact": r.fact} for r in rows]
    except Exception as exc:
        logger.warning("load_repo_user_memory failed: %s", exc)
        return []


def upsert_repo_user_memory(
    user_id: str,
    repo_id: str,
    facts: list[dict],
    db: Session,
) -> None:
    """Insert new repo-fact memory entries, skipping exact duplicates.

    Parameters
    ----------
    user_id:
        Authenticated user ID.
    repo_id:
        Repository ID.
    facts:
        List of ``{"category": str, "fact": str}`` dicts extracted by the LLM.
    db:
        Active database session.
    """
    if not facts:
        return
    try:
        existing: set[str] = {
            r.fact
            for r in db.query(RepoUserMemoryModel.fact)
            .filter_by(user_id=user_id, repo_id=repo_id)
            .all()
        }
        now = datetime.now(timezone.utc)
        new_count = 0
        for item in facts:
            fact_text = (item.get("fact") or "").strip()
            category = (item.get("category") or "general").strip()
            if not fact_text or fact_text in existing:
                continue
            db.add(RepoUserMemoryModel(
                user_id=user_id,
                repo_id=repo_id,
                category=category,
                fact=fact_text,
                created_at=now,
                updated_at=now,
            ))
            existing.add(fact_text)
            new_count += 1
        if new_count:
            db.commit()
            logger.info(
                "repo_user_memory: inserted %d new facts for user %s repo %s",
                new_count, user_id[:8], repo_id[:12],
            )
    except Exception as exc:
        logger.warning("upsert_repo_user_memory failed: %s", exc)
        db.rollback()
