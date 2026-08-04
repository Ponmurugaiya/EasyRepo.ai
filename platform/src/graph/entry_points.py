"""Entry point detection for code graph traversal.

Scores every module entity in a repository to identify likely execution
entry points using three independent signals:

  1. Filename heuristics  — language-specific well-known entry filenames
  2. Source pattern match — e.g. ``if __name__ == "__main__"`` in Python
  3. Zero in-degree       — module has outgoing CALLS edges but no incoming ones

The scores are additive so multiple signals reinforce each other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from src.storage.models import EntityModel, RelationshipModel

# ---------------------------------------------------------------------------
# Per-language heuristic configuration
# ---------------------------------------------------------------------------

_HEURISTICS: dict[str, dict] = {
    "python": {
        "filenames": {
            "main.py",
            "run.py",
            "app.py",
            "cli.py",
            "manage.py",
            "wsgi.py",
            "asgi.py",
            "server.py",
            "__main__.py",
        },
        "source_patterns": [
            r'if\s+__name__\s*==\s*["\']__main__["\']',
        ],
    },
    "typescript": {
        "filenames": {
            "index.ts",
            "main.ts",
            "server.ts",
            "app.ts",
            "index.tsx",
            "main.tsx",
        },
        "source_patterns": [
            r"app\.listen\s*\(",
            r"server\.listen\s*\(",
            r"createServer\s*\(",
            r"bootstrap\s*\(",
        ],
    },
    "markdown": {
        "filenames": set(),
        "source_patterns": [],
    },
}

# Score weights
_SCORE_FILENAME = 10
_SCORE_SOURCE_PATTERN = 5
_SCORE_ZERO_INDEGREE = 3  # weaker — lots of files have zero in-degree


@dataclass
class EntryPointResult:
    entity_id: str
    file_path: str
    name: str
    language: str
    score: int
    reasons: list[str] = field(default_factory=list)


def detect_entry_points(
    repo_id: str,
    db: Session,
    top_n: int = 10,
) -> list[EntryPointResult]:
    """Score all module entities and return the top-N most likely entry points.

    Args:
        repo_id:  Repository ID to scan.
        db:       Active SQLAlchemy session.
        top_n:    Maximum number of results to return.

    Returns:
        List of :class:`EntryPointResult` sorted by score descending.
        Only entities with score > 0 are returned.
    """
    # Fetch all module entities for the repo
    modules: list[EntityModel] = (
        db.query(EntityModel)
        .filter_by(repo_id=repo_id, type="module")
        .all()
    )

    if not modules:
        return []

    # Build set of module IDs that have AT LEAST ONE incoming CALLS edge
    # (these are NOT zero-in-degree and therefore less likely to be entry points)
    has_incoming_calls: set[str] = set(
        row[0]
        for row in db.query(RelationshipModel.target_id)
        .filter(
            RelationshipModel.repo_id == repo_id,
            RelationshipModel.type == "CALLS",
            RelationshipModel.target_id.isnot(None),
        )
        .distinct()
        .all()
        if row[0] is not None
    )

    # Build set of module IDs that have AT LEAST ONE outgoing CALLS edge
    has_outgoing_calls: set[str] = set(
        row[0]
        for row in db.query(RelationshipModel.source_id)
        .filter(
            RelationshipModel.repo_id == repo_id,
            RelationshipModel.type == "CALLS",
        )
        .distinct()
        .all()
    )

    results: list[EntryPointResult] = []

    for module in modules:
        score = 0
        reasons: list[str] = []
        lang = module.language or "python"
        rules = _HEURISTICS.get(lang, _HEURISTICS["python"])

        # --- Signal 1: filename match ---
        filename = Path(module.file_path).name
        if filename in rules["filenames"]:
            score += _SCORE_FILENAME
            reasons.append(f"filename:{filename}")

        # --- Signal 2: source pattern match ---
        source = module.source or ""
        for pattern in rules["source_patterns"]:
            if re.search(pattern, source):
                score += _SCORE_SOURCE_PATTERN
                reasons.append(f"pattern:{pattern}")
                break  # one match per module is enough

        # --- Signal 3: zero in-degree (calls others but nobody calls it) ---
        # Only counts if the node HAS outgoing calls — a node with no
        # incoming AND no outgoing edges is an isolate, not an entry point.
        if (
            module.id not in has_incoming_calls
            and module.id in has_outgoing_calls
        ):
            score += _SCORE_ZERO_INDEGREE
            reasons.append("zero-in-degree")

        if score > 0:
            results.append(
                EntryPointResult(
                    entity_id=module.id,
                    file_path=module.file_path,
                    name=module.name,
                    language=lang,
                    score=score,
                    reasons=reasons,
                )
            )

    results.sort(key=lambda r: r.score, reverse=True)
    return results[:top_n]
