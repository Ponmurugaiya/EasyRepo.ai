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
  4. "Generating embeddings for N entities via Jina AI…"
     → updated per-batch: "Embedding X/N entities…"
  5. "Saving to database…"
  done → status = "ready", progress_message = None
"""

from __future__ import annotations

import logging
import os
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

from src.embedding.config import BATCH_SIZE
from src.embedding.embedder import CodeEmbedder, CancellationRequestedError, format_entity_for_embedding
from src.extraction.entity_extractor import EntityExtractor
from src.languages import ADAPTER_REGISTRY
from src.resolution import resolve_relationships
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel
from src.storage.repo_id import canonical_source, derive_repo_id, repo_name_from_source

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'^(https?://|git@|git://)', re.IGNORECASE)


class CancellationError(RuntimeError):
    """Raised when the pipeline detects that the user cancelled indexing."""

# Extensions that the adapter registry supports (Python + TypeScript).
# Used to detect mixed-language repos and provide user-facing warnings.
_SUPPORTED_EXTENSIONS = frozenset([".py", ".ts", ".tsx"])

# Common file extensions we can meaningfully name in warnings.
_KNOWN_LANG_EXTENSIONS: dict[str, str] = {
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".java": "Java",
    ".rb": "Ruby",
    ".go": "Go",
    ".rs": "Rust",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".c": "C",
    ".cs": "C#",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".scala": "Scala",
    ".r": "R",
    ".m": "MATLAB/Objective-C",
    ".lua": "Lua",
    ".ex": "Elixir",
    ".exs": "Elixir",
    ".hs": "Haskell",
    ".ml": "OCaml",
    ".clj": "Clojure",
    ".dart": "Dart",
    ".vue": "Vue",
    ".svelte": "Svelte",
}

_SKIP_DIRS = frozenset(["node_modules", "venv", "__pycache__", ".git", ".venv", "dist", "build"])


def _is_url(source: str) -> bool:
    return bool(_URL_RE.match(source.strip()))


def _scan_languages(repo_path: Path) -> tuple[bool, list[str]]:
    """Walk *repo_path* and check which languages are present.

    Returns:
        has_supported: True if at least one Python/TypeScript file exists.
        unsupported_names: Sorted, deduplicated list of human-readable names
            for languages that are present but not yet supported.
    """
    has_supported = False
    unsupported_exts: set[str] = set()

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in files:
            ext = Path(filename).suffix.lower()
            if ext in _SUPPORTED_EXTENSIONS:
                has_supported = True
            elif ext in _KNOWN_LANG_EXTENSIONS:
                unsupported_exts.add(ext)

    unsupported_names = sorted(
        {_KNOWN_LANG_EXTENSIONS[e] for e in unsupported_exts}
    )
    return has_supported, unsupported_names


def _friendly_ingest_error(exc: Exception) -> str:
    """Map a raw pipeline exception to a clean, user-facing progress message."""
    if isinstance(exc, CancellationError):
        return "Indexing cancelled."
    msg = str(exc).lower()

    if "rate limit" in msg or "429" in msg or "too many" in msg:
        return "Indexing paused — embedding service rate limit reached. Try re-indexing shortly."
    if "jina" in msg and any(k in msg for k in ("auth", "401", "invalid", "forbidden", "403")):
        return "Indexing failed — Jina AI key is invalid or missing. Check your configuration."
    if "jina_api_key" in msg or "jina api key" in msg:
        return "Indexing failed — JINA_API_KEY is not set. Add it to your .env file."
    if "git clone" in msg or "clone failed" in msg:
        return "Could not clone the repository. Check the URL and try again."
    if "does not exist" in msg or "no such file" in msg:
        return "Repository path not found. Check the URL or local path."
    if "timeout" in msg or "timed out" in msg:
        return "Indexing timed out. The repository may be too large — try again."
    if "jina embed failed after max retries" in msg:
        return "Indexing failed — Jina AI embedding retries exhausted. Try again in a few minutes."
    if "no python or typescript files" in msg:
        return str(exc)  # already a clean user-facing message

    return "Indexing failed. Try re-indexing the repository."


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


# ── Cancellation helper ───────────────────────────────────────────────────────

def _check_cancelled(repo_id: str, session: Session) -> None:
    """Re-read the repo row and raise CancellationError if status is 'cancelled'.

    Called between pipeline stages so a user-triggered cancel takes effect
    at the next safe checkpoint without interrupting an in-flight HTTP call.
    """
    from sqlalchemy.orm import Session as _Session  # noqa: avoid circular at top
    row = session.query(RepositoryModel).filter_by(id=repo_id).first()
    if row and row.status == "cancelled":
        raise CancellationError(f"Indexing cancelled by user for repo {repo_id}")


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

        # ── Stage 1b: language scan ───────────────────────────────────────────
        # Detect what languages exist in the repo before doing any heavy work.
        # - No Python/TS files at all → fail immediately with a clear message.
        # - Python/TS present alongside unsupported languages → proceed but
        #   record a warning so the frontend can inform the user.
        has_supported, unsupported_lang_names = _scan_languages(repo_path)
        if not has_supported:
            other = ", ".join(unsupported_lang_names) if unsupported_lang_names else "other languages"
            raise RuntimeError(
                f"No Python or TypeScript files found in this repository. "
                f"We detected {other} — we're still working on support for these languages. "
                f"Try a repo that contains Python (.py) or TypeScript (.ts/.tsx) files."
            )

        # Build the warning string now so we can attach it at the end.
        lang_warning: Optional[str] = None
        if unsupported_lang_names:
            names = ", ".join(unsupported_lang_names)
            lang_warning = (
                f"We only indexed the Python and TypeScript files in this repo. "
                f"Support for {names} is still in development — "
                f"but you can explore the Python and TypeScript parts right now."
            )

        # ── Stage 2: entity extraction ────────────────────────────────────────
        t1 = time.perf_counter()
        progress("Parsing source files…", pct=10)

        extractor = EntityExtractor()
        extracted_ents, contains_rels = extractor.extract_repository(str(repo_path))

        t1_done = time.perf_counter()
        logger.info("Extraction: %d entities in %.1fs", len(extracted_ents), t1_done - t1)

        # Cancellation checkpoint — after the cheap extraction stage
        _check_cancelled(repo_id, db_session)

        # ── Stage 3: relationship resolution ─────────────────────────────────
        t2 = time.perf_counter()
        progress(f"Resolving relationships for {len(extracted_ents)} entities…", pct=25)

        resolved_rels = resolve_relationships(
            extracted_ents, str(repo_path), ADAPTER_REGISTRY
        )
        all_rels = contains_rels + resolved_rels

        t2_done = time.perf_counter()
        logger.info("Resolution: %d relationships in %.1fs", len(all_rels), t2_done - t2)

        # Cancellation checkpoint — before the slow embedding stage
        _check_cancelled(repo_id, db_session)

        # ── Stage 4: embedding ────────────────────────────────────────────────
        t3 = time.perf_counter()
        n = len(extracted_ents)
        progress(f"Generating embeddings for {n} entities via Jina AI…", pct=35)

        embedder = CodeEmbedder()
        texts = [format_entity_for_embedding(e) for e in extracted_ents]

        _last_progress_time: list[float] = [0.0]  # mutable cell for closure

        def _on_embed_progress(done: int, total: int) -> None:
            now = time.perf_counter()
            # Throttle DB writes to at most once every 1.5s so the frontend
            # poll (every 2s) always catches a fresh value, without hammering
            # the DB on large batches that complete quickly.
            if done == total or now - _last_progress_time[0] >= 1.5:
                embed_pct = 35 + int((done / total) * 55)
                progress(f"Embedding {done}/{total} entities…", pct=embed_pct)
                _last_progress_time[0] = now

        def _on_embed_wait(seconds: float) -> None:
            wait_secs = int(seconds) + 1  # round up so the UI never shows 0
            progress(
                f"Too many requests — rate limit reached, waiting {wait_secs}s…",
                pct=None,
            )

        def _should_cancel() -> bool:
            """Re-read the repo row; return True if user cancelled."""
            try:
                row = db_session.query(RepositoryModel).filter_by(id=repo_id).first()
                return row is not None and row.status == "cancelled"
            except Exception:
                return False

        all_embeddings = embedder.embed_batch(
            texts,
            on_progress=_on_embed_progress,
            on_wait=_on_embed_wait,
            should_cancel=_should_cancel,
        )

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
        # Store the language warning (if any) as a suffix so the frontend can
        # surface it without a schema change. The frontend strips it before display.
        repo.progress_message = f"|warn={lang_warning}" if lang_warning else None
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
            # Don't overwrite a user-triggered cancel with "failed"
            if isinstance(exc, (CancellationError, CancellationRequestedError)):
                repo.status = "cancelled"
                repo.progress_message = "Indexing cancelled."
            else:
                repo.status = "failed"
                repo.progress_message = _friendly_ingest_error(exc)
            db_session.commit()
        raise

    finally:
        # Always clean up the temp clone dir
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            logger.info("Cleaned up temp dir: %s", tmp_dir)
