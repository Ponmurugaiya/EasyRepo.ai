"""Long-term memory store — three-tier design.

Tiers
-----
1. user_memory            — global user preferences/background (cross-repo)
2. user_repo_preferences  — how this user works with this specific repo
3. repo_user_memory       — facts about this repo learned via this user

Public API
----------
load_user_memory(user_id, db)                    -> list[dict]
load_user_repo_preferences(user_id, repo_id, db) -> list[dict]
load_repo_user_memory(user_id, repo_id, db)      -> list[dict]

upsert_user_memory(user_id, facts, db)
upsert_user_repo_preferences(user_id, repo_id, facts, db)
upsert_repo_user_memory(user_id, repo_id, facts, db)

Each ``facts`` argument is a list of dicts: [{"category": "...", "fact": "..."}, ...]

Deduplication strategy: exact match on the ``fact`` text within the same
scope (user_id or user_id+repo_id).  If the exact fact already exists,
the insert is skipped silently.  This is intentionally simple — no
embedding-based similarity, no fuzzy matching.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.storage.models import (
    UserMemoryModel,
    UserRepoPreferenceModel,
    RepoUserMemoryModel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Load helpers
# ---------------------------------------------------------------------------

def load_user_memory(user_id: str, db: Session) -> list[dict]:
    """Return all global memory facts for a user.

    Returns
    -------
    list[dict]
        Each dict has ``category`` and ``fact`` keys.
    """
    try:
        rows = (
            db.query(UserMemoryModel)
            .filter_by(user_id=user_id)
            .order_by(UserMemoryModel.created_at.asc())
            .all()
        )
        return [{"category": r.category, "fact": r.fact} for r in rows]
    except Exception as exc:
        logger.warning("load_user_memory failed: %s", exc)
        return []


def load_user_repo_preferences(
    user_id: str, repo_id: str, db: Session
) -> list[dict]:
    """Return all preference facts for this user+repo combination.

    Returns
    -------
    list[dict]
        Each dict has ``category`` and ``fact`` keys.
    """
    try:
        rows = (
            db.query(UserRepoPreferenceModel)
            .filter_by(user_id=user_id, repo_id=repo_id)
            .order_by(UserRepoPreferenceModel.created_at.asc())
            .all()
        )
        return [{"category": r.category, "fact": r.fact} for r in rows]
    except Exception as exc:
        logger.warning("load_user_repo_preferences failed: %s", exc)
        return []


def load_repo_user_memory(
    user_id: str, repo_id: str, db: Session
) -> list[dict]:
    """Return all repo-fact memory entries for this user+repo combination.

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


# ---------------------------------------------------------------------------
# Upsert helpers — exact-match dedup before insert
# ---------------------------------------------------------------------------

def upsert_user_memory(
    user_id: str,
    facts: list[dict],
    db: Session,
) -> None:
    """Insert new global user-memory facts, skipping exact duplicates.

    Parameters
    ----------
    user_id:
        Authenticated user ID.
    facts:
        List of ``{"category": str, "fact": str}`` dicts extracted by the LLM.
    db:
        Active database session (should be a dedicated session when called
        from a background thread).
    """
    if not facts:
        return
    try:
        # Fetch existing facts once for dedup
        existing: set[str] = {
            r.fact
            for r in db.query(UserMemoryModel.fact)
            .filter_by(user_id=user_id)
            .all()
        }
        now = datetime.now(timezone.utc)
        new_count = 0
        for item in facts:
            fact_text = (item.get("fact") or "").strip()
            category = (item.get("category") or "general").strip()
            if not fact_text or fact_text in existing:
                continue
            db.add(UserMemoryModel(
                user_id=user_id,
                category=category,
                fact=fact_text,
                created_at=now,
                updated_at=now,
            ))
            existing.add(fact_text)
            new_count += 1
        if new_count:
            db.commit()
            logger.info("user_memory: inserted %d new facts for user %s", new_count, user_id[:8])
    except Exception as exc:
        logger.warning("upsert_user_memory failed: %s", exc)
        db.rollback()


def upsert_user_repo_preferences(
    user_id: str,
    repo_id: str,
    facts: list[dict],
    db: Session,
) -> None:
    """Insert new user-repo preference facts, skipping exact duplicates.

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
            for r in db.query(UserRepoPreferenceModel.fact)
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
            db.add(UserRepoPreferenceModel(
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
                "user_repo_preferences: inserted %d new facts for user %s repo %s",
                new_count, user_id[:8], repo_id[:12],
            )
    except Exception as exc:
        logger.warning("upsert_user_repo_preferences failed: %s", exc)
        db.rollback()


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
