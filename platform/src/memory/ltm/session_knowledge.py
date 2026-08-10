"""Session Knowledge — session-scoped feature cache (LTM tier 3).

Stores what the agent learned about a specific feature or topic within a
session, keyed by (repo_id, session_id, feature_name).

Stale detection: an entry written before the repository was last re-indexed
is discarded as a cache miss.  This prevents outdated knowledge from
surviving a re-index.

All operations are no-ops when session_id is None — preserves full backward
compatibility with clients that do not send a session_id.

Public API
----------
lookup(repo_id, session_id, intent, repo, db)           -> ConversationMemoryModel | None
write(repo_id, session_id, ltm_entry, repo, db)         -> None
inject_ltm(final_context_text, ltm_entry)               -> str
lookup_by_feature(repo_id, session_id, feature, repo, db) -> ConversationMemoryModel | None
write_feature(repo_id, session_id, feature, summary, repo, db, ...) -> None
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.storage.models import ConversationMemoryModel, RepositoryModel

logger = logging.getLogger(__name__)


def lookup(
    repo_id: str,
    session_id: Optional[str],
    intent: str,
    repo: RepositoryModel,
    db: Session,
) -> Optional[ConversationMemoryModel]:
    """Look up a session knowledge entry by (repo_id, session_id, feature_name).

    Returns None when:
    - session_id is absent
    - no matching entry exists
    - the entry was written before the repository was last re-indexed (stale)

    Parameters
    ----------
    repo_id:
        Target repository ID.
    session_id:
        Client-provided UUID scoping this session's knowledge.
    intent:
        Query intent string used as the feature_name lookup key.
    repo:
        Current repository ORM row (used for stale detection).
    db:
        Active database session.
    """
    if not session_id:
        return None

    try:
        entry: Optional[ConversationMemoryModel] = (
            db.query(ConversationMemoryModel)
            .filter_by(
                repo_id=repo_id,
                session_id=session_id,
                feature_name=intent,
                exploration_status="complete",
            )
            .order_by(ConversationMemoryModel.created_at.desc())
            .first()
        )

        if entry is None:
            logger.debug("Session knowledge miss: repo=%s session=%s intent=%s",
                         repo_id, session_id, intent)
            return None

        if entry.repo_indexed_at is None:
            logger.debug("Session knowledge entry has no repo_indexed_at — treating as stale")
            return None
        if repo.indexed_at and repo.indexed_at > entry.repo_indexed_at:
            logger.info(
                "Session knowledge stale: repo re-indexed at %s, entry written at %s — discarding.",
                repo.indexed_at,
                entry.repo_indexed_at,
            )
            return None

        logger.info(
            "Session knowledge hit: repo=%s session=%s feature=%s confidence=%s",
            repo_id, session_id, entry.feature_name, entry.confidence,
        )
        return entry

    except Exception as exc:
        logger.warning("session_knowledge.lookup failed: %s", exc)
        return None


def write(
    repo_id: str,
    session_id: str,
    ltm_entry: dict,
    repo: RepositoryModel,
    db: Session,
) -> None:
    """Write a session knowledge entry produced by the Answer Agent.

    ``ltm_entry`` is expected to contain:
      - feature_name: str
      - confidence: "high" | "medium" | "low"
      - exploration_status: "partial" | "complete"
      - summary: str
      - source_entity_ids: list[str] (optional)

    Does nothing if session_id is absent or if the entry dict is malformed.
    Any DB error is caught and logged without raising.
    """
    if not session_id:
        return

    required = {"feature_name", "summary"}
    if not required.issubset(ltm_entry.keys()):
        logger.warning("session_knowledge.write skipped — missing required keys: %s",
                       ltm_entry.keys())
        return

    try:
        record = ConversationMemoryModel(
            repo_id=repo_id,
            session_id=session_id,
            feature_name=ltm_entry["feature_name"],
            summary=ltm_entry["summary"],
            source_entity_ids=ltm_entry.get("source_entity_ids", []),
            graph_paths=ltm_entry.get("graph_paths", []),
            confidence=ltm_entry.get("confidence", "medium"),
            exploration_status=ltm_entry.get("exploration_status", "partial"),
            repo_indexed_at=repo.indexed_at,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.flush()
        logger.info(
            "Session knowledge written: repo=%s session=%s feature=%s confidence=%s",
            repo_id,
            session_id,
            ltm_entry["feature_name"],
            ltm_entry.get("confidence", "medium"),
        )
    except Exception as exc:
        logger.warning("session_knowledge.write failed: %s", exc)


def inject_ltm(final_context_text: str, ltm_entry: ConversationMemoryModel) -> str:
    """Prepend the session knowledge summary to the rendered context text."""
    ltm_block = (
        f"\n=== LONG-TERM MEMORY (from earlier in this session) ===\n"
        f"Feature: {ltm_entry.feature_name}\n"
        f"Confidence: {ltm_entry.confidence}  |  "
        f"Exploration: {ltm_entry.exploration_status}\n"
        f"{ltm_entry.summary}\n"
        f"=== END LONG-TERM MEMORY ===\n"
    )
    return ltm_block + final_context_text


def lookup_by_feature(
    repo_id: str,
    session_id: Optional[str],
    feature_name: str,
    repo: RepositoryModel,
    db: Session,
) -> Optional[ConversationMemoryModel]:
    """Look up a session knowledge entry by exact feature_name.

    Used by the overview pipeline to check folder-level cache entries
    (feature_name = "folder:src/api") and repo-level cache
    (feature_name = "repo_overview" | "repo_detailed").
    """
    if not session_id:
        return None
    try:
        entry = (
            db.query(ConversationMemoryModel)
            .filter_by(
                repo_id=repo_id,
                session_id=session_id,
                feature_name=feature_name,
                exploration_status="complete",
            )
            .order_by(ConversationMemoryModel.created_at.desc())
            .first()
        )
        if entry is None:
            return None
        if entry.repo_indexed_at is None:
            logger.debug("Session knowledge entry (overview) has no repo_indexed_at — stale")
            return None
        if repo.indexed_at and repo.indexed_at > entry.repo_indexed_at:
            logger.info("Session knowledge stale (overview): feature=%s", feature_name)
            return None
        return entry
    except Exception as exc:
        logger.warning("session_knowledge.lookup_by_feature failed: %s", exc)
        return None


def write_feature(
    repo_id: str,
    session_id: str,
    feature_name: str,
    summary: str,
    repo: RepositoryModel,
    db: Session,
    confidence: str = "high",
    source_entity_ids: Optional[list] = None,
) -> None:
    """Write a named session knowledge entry (folder summary or repo overview)."""
    if not session_id:
        return
    try:
        record = ConversationMemoryModel(
            repo_id=repo_id,
            session_id=session_id,
            feature_name=feature_name,
            summary=summary,
            source_entity_ids=source_entity_ids or [],
            graph_paths=[],
            confidence=confidence,
            exploration_status="complete",
            repo_indexed_at=repo.indexed_at,
            created_at=datetime.now(timezone.utc),
        )
        db.add(record)
        db.flush()
        logger.info(
            "Session knowledge written (overview): repo=%s feature=%s",
            repo_id, feature_name,
        )
    except Exception as exc:
        logger.warning("session_knowledge.write_feature failed: %s", exc)
