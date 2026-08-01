"""Repository ingestion pipeline — clone (if URL), extract, resolve, embed, persist.

GitHub URL flow
---------------
If ``repo_path_or_url`` looks like a URL (http/https/git), the pipeline
clones it into a temporary directory with ``git clone --depth 1``, runs
the full pipeline against that directory, then deletes the temp dir.

Local path flow
---------------
If it's a local path the directory is used directly (no clone, no cleanup).

Progress tracking
-----------------
Each major stage writes a human-readable ``progress_message`` to the
``repositories`` row so the frontend can display live status by polling
``GET /repositories/{id}/status``.

Stages (in order):
  1. "Cloning repository…"
  2. "Parsing source files…"
  3. "Resolving relationships for N entities…"
  4. "Generating embeddings for N entities via Voyage AI…"
     → updated per-batch: "Embedding X/N entities…"
  5. "Saving to database…"
  done → status = "ready", progress_message = None
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.embedding.embedder import CodeEmbedder, format_entity_for_embedding
from src.extraction.entity_extractor import EntityExtractor
from src.languages import ADAPTER_REGISTRY
from src.resolution import resolve_relationships
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel
from src.storage.repo_id import canonical_source, derive_repo_id, repo_name_from_source

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'^(https?://|git@|git://)', re.IGNORECASE)


def _is_url(source: str) -> bool:
    return bool(_URL_RE.match(source.strip()))


def _clone(url: str, progress_cb) -> str:
    """Clone *url* into a fresh temp directory and return its path."""
    tmp = tempfile.mkdtemp(prefix="easyrepo_")
    progress_cb(f"Cloning repository from {url.split('github.com/')[-1]}…", pct=2)
    logger.info("git clone --depth 1 %s -> %s", url, tmp)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--", url, tmp],
        capture_output=True,
        text=True,
        timeout=300,       # 5-minute hard limit for very large repos
    )
    if result.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(
            f"git clone failed (exit {result.returncode}): {result.stderr.strip()[:400]}"
        )
    logger.info("Clone complete: %s", tmp)
    return tmp


# ── Progress helper ───────────────────────────────────────────────────────────

def _set_progress(
    repo: RepositoryModel,
    session: Session,
    message: str,
    *,
    pct: Optional[int] = None,
    commit: bool = True,
) -> None:
    """Write a progress message + optional percentage to the repo row.

    Encodes percentage as a suffix ``|pct=42`` so the frontend can parse it
    without a schema change. The frontend strips the suffix before display.
    """
    full = f"{message}|pct={pct}" if pct is not None else message
    repo.progress_message = full
    logger.info("[%s] %s", repo.id[:8], full)
    if commit:
        session.commit()


# ── Public entry point ────────────────────────────────────────────────────────

def ingest_repository(
    repo_path_or_url: str,
    db_session: Session,
    repo_id: Optional[str] = None,
    repo_name: Optional[str] = None,
) -> RepositoryModel:
    """Clone (if URL), extract, embed, and persist a repository.

    Args:
        repo_path_or_url: Local path OR a GitHub/git URL.
        db_session: Active SQLAlchemy session.
        repo_id: Optional ID override.
        repo_name: Optional name override.

    Returns:
        The updated RepositoryModel with status='ready'.
    """
    t0 = time.perf_counter()

    if not repo_name:
        repo_name = repo_name_from_source(repo_path_or_url)
    if not repo_id:
        repo_id = derive_repo_id(repo_path_or_url)
    canon = canonical_source(repo_path_or_url)

    # ── Stage 0: initialise DB row ────────────────────────────────────────────
    repo = db_session.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        repo = RepositoryModel(
            id=repo_id,
            url_or_path=repo_path_or_url,
            canonical_url=canon,
            name=repo_name,
            status="indexing",
        )
        db_session.add(repo)
    else:
        repo.url_or_path = repo_path_or_url
        repo.canonical_url = canon
        repo.name = repo_name
        repo.status = "indexing"

    # Always wipe any stale entities/relationships from a previous (possibly
    # failed or partial) run before inserting new ones. This prevents
    # IntegrityError on duplicate entity IDs when re-indexing.
    db_session.execute(
        delete(RelationshipModel).where(RelationshipModel.repo_id == repo_id)
    )
    db_session.execute(
        delete(EntityModel).where(EntityModel.repo_id == repo_id)
    )
    db_session.commit()

    # progress callback — captures repo/session in closure
    def progress(msg: str, pct: Optional[int] = None) -> None:
        _set_progress(repo, db_session, msg, pct=pct)

    # ── Stage 1: clone if URL, otherwise resolve local path ──────────────────
    tmp_dir: Optional[str] = None
    try:
        if _is_url(repo_path_or_url):
            tmp_dir = _clone(repo_path_or_url, progress)
            repo_path = Path(tmp_dir)
        else:
            repo_path = Path(repo_path_or_url).resolve()
            if not repo_path.exists():
                raise FileNotFoundError(
                    f"Local path does not exist: {repo_path}"
                )
            progress("Reading local repository…", pct=5)

        # ── Stage 2: entity extraction ────────────────────────────────────────
        t1 = time.perf_counter()
        progress("Parsing source files…", pct=10)

        extractor = EntityExtractor()
        extracted_ents, contains_rels = extractor.extract_repository(str(repo_path))

        t1_done = time.perf_counter()
        logger.info("Extraction: %d entities in %.1fs", len(extracted_ents), t1_done - t1)

        # ── Stage 3: relationship resolution ─────────────────────────────────
        t2 = time.perf_counter()
        progress(f"Resolving relationships for {len(extracted_ents)} entities…", pct=25)

        resolved_rels = resolve_relationships(
            extracted_ents, str(repo_path), ADAPTER_REGISTRY
        )
        all_rels = contains_rels + resolved_rels

        t2_done = time.perf_counter()
        logger.info("Resolution: %d relationships in %.1fs", len(all_rels), t2_done - t2)

        # ── Stage 4: embedding ────────────────────────────────────────────────
        t3 = time.perf_counter()
        n = len(extracted_ents)
        progress(f"Generating embeddings for {n} entities via Voyage AI…", pct=35)

        embedder = CodeEmbedder()
        texts = [format_entity_for_embedding(e) for e in extracted_ents]

        all_embeddings: list[list[float]] = []
        batch_size = 128
        for start in range(0, n, batch_size):
            chunk = texts[start : start + batch_size]
            chunk_embeddings = embedder.embed_batch(chunk, batch_size=batch_size)
            all_embeddings.extend(chunk_embeddings)
            done = min(start + batch_size, n)
            # Map embedding progress to 35–90% of total
            embed_pct = 35 + int((done / n) * 55)
            progress(f"Embedding {done}/{n} entities…", pct=embed_pct)

        t3_done = time.perf_counter()
        logger.info(
            "Embedding: %d vectors in %.1fs (%.0f ents/s)",
            n, t3_done - t3, n / max(t3_done - t3, 0.001),
        )

        # ── Stage 5: database insert ──────────────────────────────────────────
        t4 = time.perf_counter()
        progress("Saving to database…", pct=92)

        sorted_ents = sorted(extracted_ents, key=lambda e: (e.id.count("."), e.id))
        ent_id_to_vec = {ent.id: vec for ent, vec in zip(extracted_ents, all_embeddings)}
        known_ids = {e.id for e in extracted_ents}

        db_entity_dicts = [
            {
                "id": ent.id,
                "repo_id": repo_id,
                "type": ent.type,
                "name": ent.name,
                "file_path": ent.file_path,
                "start_line": ent.start_line,
                "end_line": ent.end_line,
                "parent_id": ent.parent_id if ent.parent_id in known_ids else None,
                "language": ent.language,
                "has_docstring": ent.has_docstring,
                "source": ent.source,
                "embedding": ent_id_to_vec.get(ent.id),
            }
            for ent in sorted_ents
        ]

        # Use ON CONFLICT DO NOTHING as a safety net in case a concurrent
        # run or a retry produces duplicate entity IDs.
        stmt = pg_insert(EntityModel).values(db_entity_dicts)
        stmt = stmt.on_conflict_do_nothing(index_elements=["id"])
        db_session.execute(stmt)
        db_session.flush()
        db_entities = db_entity_dicts  # for count logging

        db_rels = [
            RelationshipModel(
                repo_id=repo_id,
                source_id=rel.source_id,
                target_id=rel.target_id if rel.target_id in known_ids else None,
                external_target_name=None if rel.target_id in known_ids else rel.target_id,
                type=rel.type,
                file_path=rel.file_path,
                line=rel.line,
            )
            for rel in all_rels
        ]
        db_session.add_all(db_rels)

        t4_done = time.perf_counter()
        logger.info("DB insert: %.1fs", t4_done - t4)

        # ── Done ──────────────────────────────────────────────────────────────
        repo.status = "ready"
        repo.indexed_at = datetime.now(timezone.utc)
        repo.progress_message = None
        db_session.commit()

        total = time.perf_counter() - t0
        logger.info(
            "Ingested '%s' in %.1fs | entities=%d relationships=%d "
            "clone+extract=%.1fs resolve=%.1fs embed=%.1fs db=%.1fs",
            repo_name, total,
            len(db_entities), len(db_rels),
            t1_done - t0, t2_done - t2, t3_done - t3, t4_done - t4,
        )
        return repo

    except Exception as exc:
        db_session.rollback()
        logger.error("Ingestion failed for '%s': %s", repo_name, exc, exc_info=True)
        repo = db_session.query(RepositoryModel).filter_by(id=repo_id).first()
        if repo:
            repo.status = "failed"
            repo.progress_message = f"Failed: {str(exc)[:200]}"
            db_session.commit()
        raise

    finally:
        # Always clean up the temp clone dir
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info("Cleaned up temp dir: %s", tmp_dir)
