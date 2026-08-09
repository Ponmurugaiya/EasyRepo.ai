"""Long-Term Memory (LTM) store.

Reads and writes ``ConversationMemoryModel`` rows.  Keyed by
(repo_id, session_id, feature_name).

Stale detection: an LTM entry written before the repository was last
re-indexed is discarded as a cache miss.  This prevents outdated knowledge
from surviving a re-index.

All LTM operations are no-ops when ``session_id`` is None — this preserves
full backward compatibility with clients that do not send a session_id.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

from src.storage.models import ConversationMemoryModel, RepositoryModel

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def lookup(
    repo_id: str,
    session_id: Optional[str],
    intent: str,
    repo: RepositoryModel,
    db: Session,
) -> Optional[ConversationMemoryModel]:
    """Look up an LTM entry by (repo_id, session_id, feature_name).

    Returns None when:
    - session_id is absent (LTM disabled)
    - no matching entry exists
    - the entry was written before the repository was last re-indexed (stale)

    Parameters
    ----------
    repo_id:
        Target repository ID.
    session_id:
        Client-provided UUID scoping this session's LTM.
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
            logger.debug("LTM cache miss: no entry for repo=%s session=%s intent=%s",
                         repo_id, session_id, intent)
            return None

        # Stale detection: entry with no recorded index time is unsafe — treat as stale
        if entry.repo_indexed_at is None:
            logger.debug("LTM entry has no repo_indexed_at — treating as stale")
            return None
        if repo.indexed_at and repo.indexed_at > entry.repo_indexed_at:
            logger.info(
                "LTM cache stale: repo re-indexed at %s, entry written at %s — discarding.",
                repo.indexed_at,
                entry.repo_indexed_at,
            )
            return None

        logger.info(
            "LTM cache hit: repo=%s session=%s feature=%s confidence=%s status=%s",
            repo_id, session_id, entry.feature_name, entry.confidence, entry.exploration_status,
        )
        return entry

    except Exception as exc:
        logger.warning("LTM lookup failed: %s", exc)
        return None


def write(
    repo_id: str,
    session_id: str,
    ltm_entry: dict,
    repo: RepositoryModel,
    db: Session,
) -> None:
    """Write an LTM entry produced by the Answer Agent.

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
        logger.warning("LTM write skipped — missing required keys: %s", ltm_entry.keys())
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
            "LTM written: repo=%s session=%s feature=%s confidence=%s status=%s",
            repo_id,
            session_id,
            ltm_entry["feature_name"],
            ltm_entry.get("confidence", "medium"),
            ltm_entry.get("exploration_status", "partial"),
        )
    except Exception as exc:
        logger.warning("LTM write failed: %s", exc)


def inject_ltm(final_context_text: str, ltm_entry: ConversationMemoryModel) -> str:
    """Prepend the LTM summary to the rendered context text."""
    ltm_block = (
        f"\n=== LONG-TERM MEMORY (from previous session turns) ===\n"
        f"Feature: {ltm_entry.feature_name}\n"
        f"Confidence: {ltm_entry.confidence}  |  "
        f"Exploration: {ltm_entry.exploration_status}\n"
        f"{ltm_entry.summary}\n"
        f"=== END LONG-TERM MEMORY ===\n"
    )
    return ltm_block + final_context_text


# ---------------------------------------------------------------------------
# Overview-specific helpers — folder and repo-level summary cache
# ---------------------------------------------------------------------------

def lookup_by_feature(
    repo_id: str,
    session_id: Optional[str],
    feature_name: str,
    repo: RepositoryModel,
    db: Session,
) -> Optional[ConversationMemoryModel]:
    """Look up any LTM entry by exact feature_name (not intent mapping).

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
        # Stale detection: entry with no recorded index time is unsafe
        if entry.repo_indexed_at is None:
            logger.debug("LTM entry has no repo_indexed_at (overview) — treating as stale")
            return None
        if repo.indexed_at and repo.indexed_at > entry.repo_indexed_at:
            logger.info("LTM stale (overview): feature=%s", feature_name)
            return None
        return entry
    except Exception as exc:
        logger.warning("LTM lookup_by_feature failed: %s", exc)
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
    """Write a named LTM entry (folder summary or repo overview summary)."""
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
            "LTM written (overview): repo=%s feature=%s",
            repo_id, feature_name,
        )
    except Exception as exc:
        logger.warning("LTM write_feature failed: %s", exc)
