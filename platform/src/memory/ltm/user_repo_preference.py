"""User-Repo Preference Memory — user × repo preference facts (LTM tier 5).

Stores facts about how a specific user works with a specific repository.
These are about the user's *relationship* with the codebase, not the
codebase itself.

Examples:
  - "User is new to this repo's auth module"
  - "User prefers to work on the API layer, not the DB layer"
  - "User has reviewed this repo before and knows the GraphQL migration happened"

Scope:   (user_id, repo_id)
Expires: never
Written: by summarize_after_turn() after each conversation exchange

Public API
----------
load_user_repo_preferences(user_id, repo_id, db)             -> list[dict]
upsert_user_repo_preferences(user_id, repo_id, facts, db)    -> None
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.storage.models import UserRepoPreferenceModel

logger = logging.getLogger(__name__)


def load_user_repo_preferences(
    user_id: str, repo_id: str, db: Session
) -> list[dict]:
    """Return all preference facts for this user + repo combination.

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
                "user_repo_preference: inserted %d new facts for user %s repo %s",
                new_count, user_id[:8], repo_id[:12],
            )
    except Exception as exc:
        logger.warning("upsert_user_repo_preferences failed: %s", exc)
        db.rollback()
