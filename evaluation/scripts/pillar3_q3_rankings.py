"""Pillar 3 — Q3 Ranking Detail
Prints full ranked entity list for the Q3 disambiguation query with score gap analysis.
Canonical location: evaluation/scripts/pillar3_q3_rankings.py

Usage (from EasyRepo/):
    python evaluation/scripts/pillar3_q3_rankings.py --repo-id <uuid>
"""
import argparse
import os
import sys
from pathlib import Path

# ── Path setup: evaluation/scripts/ → evaluation/ → EasyRepo/ → EasyRepo/platform/ ──
_HERE = Path(__file__).resolve()
_PLATFORM_DIR = _HERE.parent.parent.parent / "platform"
if str(_PLATFORM_DIR) not in sys.path:
    sys.path.insert(0, str(_PLATFORM_DIR))

# ── Load .env from EasyRepo root ──
_ENV_PATH = _HERE.parent.parent.parent / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

from src.retrieval import search
from src.storage.db import get_session

Q = "Is there any function in this codebase that has no dependencies on other code?"

ISOLATED_IDS = {
    "py.utils.formatting.format_user_record",
    "py.utils.formatting.format_audit_log",
    "py.utils.formatting.truncate_text",
    "py.utils.formatting",
    "py.models.base.BaseModel.validate",
    "py.models.base.BaseModel.get_metadata",
    "py.models.base.BaseModel.to_dict",
    "py.interfaces.repository.Repository.save",
    "py.interfaces.repository.Repository.find_by_id",
    "py.interfaces.repository.Repository.delete",
}


def run(db_url: str, repo_id: str) -> None:
    with get_session(db_url) as session:
        results = search(query=Q, repo_id=repo_id, top_k=62, db_session=session)
        print(f'=== VECTOR SEARCH RANKING FOR Q3: "{Q}" ===\n')
        print(f"repo_id: {repo_id}\n")
        for r in results:
            is_iso = r.entity.id in ISOLATED_IDS
            tag = " [ISOLATED ENTITY]" if is_iso else ""
            print(f"Rank {r.rank:2d} | Score: {r.score:.4f} | {r.entity.id} ({r.entity.type}){tag}")

        # Score gap between rank-1 and rank-2
        if len(results) >= 2:
            gap = results[0].score - results[1].score
            print(f"\nScore gap rank-1 vs rank-2: {gap:.4f} (target: ≥ 0.02)")
            if gap >= 0.02:
                print("RANKING CHECK PASSED: format_audit_log has sufficient score gap over rank-2")
            else:
                print("RANKING CHECK WARNING: score gap < 0.02 — disambiguation may be marginal")


def main():
    db_url_default = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
    )
    parser = argparse.ArgumentParser(
        description="Print full Q3 vector search ranking for disambiguation audit."
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        required=True,
        help="Repository UUID as returned by POST /repositories.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=db_url_default,
        help="PostgreSQL connection URL. Defaults to DATABASE_URL env var.",
    )
    args = parser.parse_args()
    run(args.db_url, args.repo_id)


if __name__ == "__main__":
    main()
