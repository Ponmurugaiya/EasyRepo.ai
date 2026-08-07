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
            return None

        # Stale detection: discard if repo was re-indexed after LTM was written
        if repo.indexed_at and entry.repo_indexed_at:
            if repo.indexed_at > entry.repo_indexed_at:
                logger.debug(
                    "LTM cache miss (stale): repo re-indexed at %s, "
                    "entry written at %s — discarding.",
                    repo.indexed_at,
                    entry.repo_indexed_at,
                )
                return None

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
        db.commit()
        logger.debug(
            "LTM written: repo=%s session=%s feature=%s status=%s",
            repo_id,
            session_id,
            ltm_entry["feature_name"],
            ltm_entry.get("exploration_status"),
        )
    except Exception as exc:
        logger.warning("LTM write failed: %s", exc)
        db.rollback()


def inject_ltm(final_context_text: str, ltm_entry: ConversationMemoryModel) -> str:
    """Prepend the LTM summary to the rendered context text.

    This surfaces the previously cached knowledge before the newly retrieved
    context so the Answer Agent can reason from both.
    """
    ltm_block = (
        f"\n=== LONG-TERM MEMORY (from previous session turns) ===\n"
        f"Feature: {ltm_entry.feature_name}\n"
        f"Confidence: {ltm_entry.confidence}  |  "
        f"Exploration: {ltm_entry.exploration_status}\n"
        f"{ltm_entry.summary}\n"
        f"=== END LONG-TERM MEMORY ===\n"
    )
    return ltm_block + final_context_text
