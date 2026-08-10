"""User Memory — global user facts (LTM tier 4).

Stores facts about the user that apply everywhere, regardless of which
repository they are working with.

Examples:
  - "Prefers functional style"
  - "I'm the backend lead"
  - "I don't like verbose explanations"

Scope:   user_id  (global)
Expires: never
Written: by summarize_after_turn() after each conversation exchange

Public API
----------
load_user_memory(user_id, db)             -> list[dict]
upsert_user_memory(user_id, facts, db)    -> None
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.storage.models import UserMemoryModel

logger = logging.getLogger(__name__)


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
        Active database session.
    """
    if not facts:
        return
    try:
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
            logger.info("user_memory: inserted %d new facts for user %s",
                        new_count, user_id[:8])
    except Exception as exc:
        logger.warning("upsert_user_memory failed: %s", exc)
        db.rollback()
