"""Repository management router — ingestion, metadata, and access control."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.dependencies import (
    get_accessible_repository,
    get_current_user,
    get_db,
    get_db_url,
    grant_repo_access,
    require_owner,
)
from src.api.schemas import RepositoryCreateRequest, RepositoryResponse, RepositoryStatusResponse
from src.jobs.queue import ingest_repo_task
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel, UserModel, UserRepoModel
from src.storage.repo_id import canonical_source, derive_repo_id, repo_name_from_source

router = APIRouter()

_limiter = Limiter(key_func=get_remote_address)
_RATE_INGEST  = os.environ.get("RATE_LIMIT_INGEST",  "10/minute")
_RATE_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")


def _extract_language_warning(progress_message: Optional[str]) -> Optional[str]:
    """Extract the ``|warn=<text>`` suffix stored by the ingestion pipeline.

    Returns the warning text, or ``None`` if no warning was encoded.
    The caller should strip this suffix from *progress_message* before
    displaying it to users.
    """
    if not progress_message:
        return None
    idx = progress_message.find("|warn=")
    if idx == -1:
        return None
    return progress_message[idx + 6:] or None


# ---------------------------------------------------------------------------
# Local schemas
# ---------------------------------------------------------------------------

class AccessGrantRequest(BaseModel):
    user_id: str = Field(..., description="User ID to grant access to")
    role: str = Field("viewer", description="Role to grant: 'owner' or 'viewer'")


class AccessGrantResponse(BaseModel):
    user_id: str
    repo_id: str
    role: str


class RepoAccessListResponse(BaseModel):
    repo_id: str
    grants: list[AccessGrantResponse]


# ---------------------------------------------------------------------------
# Repository listing (user's own repos)
# ---------------------------------------------------------------------------

@router.get("", response_model=list[RepositoryResponse])
@_limiter.limit(_RATE_DEFAULT)
async def list_repositories(
    request: Request,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> list[RepositoryResponse]:
    """List repositories accessible to the calling user.

    - Authenticated user: returns only repos the user has a grant for.
    - Anonymous (no token / auth disabled): returns all repos.

    Used by the frontend to sync the sidebar after a dev login so that stale
    anonymous-session repos the user has no access to are removed.
    """
    if current_user is None:
        # Anonymous — return everything (original open-access behaviour)
        repos = db.query(RepositoryModel).all()
    else:
        grants = db.query(UserRepoModel).filter_by(user_id=current_user.id).all()
        repo_ids = {g.repo_id for g in grants}
        repos = db.query(RepositoryModel).filter(RepositoryModel.id.in_(repo_ids)).all()

    results = []
    for repo in repos:
        entity_count = (
            db.query(func.count(EntityModel.id)).filter_by(repo_id=repo.id).scalar()
        )
        relationship_count = (
            db.query(func.count(RelationshipModel.id)).filter_by(repo_id=repo.id).scalar()
        )
        results.append(RepositoryResponse(
            repo_id=repo.id,
            name=repo.name,
            status=repo.status,
            url_or_path=repo.url_or_path,
            entity_count=entity_count,
            relationship_count=relationship_count,
            indexed_at=repo.indexed_at.isoformat() if repo.indexed_at else None,
        ))
    return results


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------

@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_202_ACCEPTED)
@_limiter.limit(_RATE_INGEST)
async def create_repository(
    request: Request,
    body: RepositoryCreateRequest,
    db: Session = Depends(get_db),
    db_url: str = Depends(get_db_url),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> RepositoryResponse:
    """Enqueue a repository for background ingestion via Procrastinate.

    Access control on ingest
    ------------------------
    - New repo: caller is automatically granted ``owner`` role (if auth enabled).
    - Repo already ``ready`` (shared index): caller is automatically granted
      ``viewer`` role and the existing indexed data is returned immediately.
    - Repo not ready (pending/indexing/failed): re-queued; caller's existing
      role is preserved, or ``owner`` granted if they have no record yet.
    """
    canon = canonical_source(body.source)
    repo_id = derive_repo_id(body.source)
    repo_name = repo_name_from_source(body.source)

    # Deduplication lookup — canonical_url first, then repo_id
    repo = (
        db.query(RepositoryModel)
        .filter(RepositoryModel.canonical_url == canon)
        .first()
    ) or db.query(RepositoryModel).filter_by(id=repo_id).first()

    if repo and repo.status == "ready":
        # Shared index already exists — grant viewer access to caller if needed
        if current_user:
            _ensure_access(current_user.id, repo.id, default_role="viewer", db=db)
            db.commit()

        entity_count = (
            db.query(func.count(EntityModel.id)).filter_by(repo_id=repo.id).scalar()
        )
        relationship_count = (
            db.query(func.count(RelationshipModel.id)).filter_by(repo_id=repo.id).scalar()
        )
        return RepositoryResponse(
            repo_id=repo.id,
            name=repo.name,
            status=repo.status,
            url_or_path=repo.url_or_path,
            entity_count=entity_count,
            relationship_count=relationship_count,
            indexed_at=repo.indexed_at.isoformat() if repo.indexed_at else None,
        )

    if repo:
        # Exists but not ready — reset and re-queue
        repo.status = "pending"
        repo.indexed_at = None
        repo.canonical_url = canon
    else:
        repo = RepositoryModel(
            id=repo_id,
            url_or_path=body.source,
            canonical_url=canon,
            name=repo_name,
            status="pending",
        )
        db.add(repo)

    db.flush()  # ensure repo.id is available before granting access

    # Grant owner to the submitting user (first indexer = owner)
    if current_user:
        _ensure_access(current_user.id, repo.id, default_role="owner", db=db)

    db.commit()

    # Defer the ingestion job to the Procrastinate worker.
    # If the connector isn't open yet (e.g. first request on startup), fall
    # back to running the ingestion directly in a thread pool so the job
    # always executes regardless of worker state.
    import logging as _logging
    _log = _logging.getLogger(__name__)
    deferred = False
    try:
        await ingest_repo_task.defer_async(
            repo_path_or_url=body.source,
            db_url=db_url,
            repo_id=repo.id,
            repo_name=repo.name,
        )
        deferred = True
        _log.info("Deferred ingestion job for %s via Procrastinate", repo.id)
    except Exception as exc:
        _log.warning(
            "Procrastinate defer failed for %s (%s) — running in thread fallback.",
            repo.id, exc,
        )

    if not deferred:
        # Fallback: run directly in asyncio thread pool so the API returns
        # immediately and ingestion runs concurrently.
        import asyncio
        import concurrent.futures
        from src.ingestion.pipeline import ingest_repository
        from src.storage.db import get_session

        _source = body.source
        _db_url = db_url
        _repo_id = repo.id
        _repo_name = repo.name

        def _run() -> None:
            try:
                with get_session(_db_url) as session:
                    ingest_repository(
                        repo_path_or_url=_source,
                        db_session=session,
                        repo_id=_repo_id,
                        repo_name=_repo_name,
                    )
            except Exception as _exc:
                _log.error("Thread-fallback ingestion failed for %s: %s", _repo_id, _exc, exc_info=True)

        # get_running_loop() is safer than get_event_loop() inside an async
        # context, and we must retain a reference to the Future so the GC
        # does not cancel it before ingestion finishes.
        _loop = asyncio.get_running_loop()
        _future = _loop.run_in_executor(None, _run)
        # Fire-and-forget: add a done-callback just to log unexpected errors
        # that slip past the try/except inside _run (shouldn't happen, but safe).
        def _on_done(fut: concurrent.futures.Future) -> None:
            if not fut.cancelled() and fut.exception():
                _log.error("Executor future raised for %s: %s", _repo_id, fut.exception())
        _future.add_done_callback(_on_done)

    return RepositoryResponse(
        repo_id=repo.id,
        name=repo.name,
        status=repo.status,
        url_or_path=repo.url_or_path,
        entity_count=0,
        relationship_count=0,
        indexed_at=None,
    )


def _ensure_access(user_id: str, repo_id: str, default_role: str, db: Session) -> None:
    """Grant *default_role* if no access record exists yet.  Never downgrades."""
    existing = db.query(UserRepoModel).filter_by(user_id=user_id, repo_id=repo_id).first()
    if not existing:
        grant_repo_access(user_id, repo_id, default_role, db)
    # If they already have owner and we'd grant viewer, keep owner — no downgrade.


# ---------------------------------------------------------------------------
# Cancel indexing
# ---------------------------------------------------------------------------

@router.post("/{repo_id}/cancel", status_code=status.HTTP_200_OK)
@_limiter.limit(_RATE_DEFAULT)
async def cancel_repository_indexing(
    request: Request,
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> dict:
    """Cancel an in-progress indexing job for a repository.

    Sets ``status = 'cancelled'`` on the repository row. The running
    pipeline worker checks this flag between stages and stops gracefully.
    Has no effect if the repo is already ``ready``, ``failed``, or
    ``cancelled``.
    """
    repo = get_accessible_repository(repo_id, db, current_user)

    if repo.status not in ("pending", "indexing"):
        return {"repo_id": repo_id, "status": repo.status, "cancelled": False}

    repo.status = "cancelled"
    repo.progress_message = "Indexing cancelled."
    db.commit()

    import logging as _log
    _log.getLogger(__name__).info("Indexing cancelled by user for repo %s", repo_id)
    return {"repo_id": repo_id, "status": "cancelled", "cancelled": True}


# ---------------------------------------------------------------------------
# Repository metadata
# ---------------------------------------------------------------------------

@router.get("/{repo_id}", response_model=RepositoryResponse)
@_limiter.limit(_RATE_DEFAULT)
async def get_repository_endpoint(
    request: Request,
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> RepositoryResponse:
    """Return metadata for a repository the caller has access to."""
    repo = get_accessible_repository(repo_id, db, current_user)
    entity_count = (
        db.query(func.count(EntityModel.id)).filter_by(repo_id=repo_id).scalar()
    )
    relationship_count = (
        db.query(func.count(RelationshipModel.id)).filter_by(repo_id=repo_id).scalar()
    )
    lang_warning = _extract_language_warning(repo.progress_message)
    return RepositoryResponse(
        repo_id=repo.id,
        name=repo.name,
        status=repo.status,
        url_or_path=repo.url_or_path,
        entity_count=entity_count,
        relationship_count=relationship_count,
        indexed_at=repo.indexed_at.isoformat() if repo.indexed_at else None,
        language_warning=lang_warning,
    )


@router.get("/{repo_id}/status", response_model=RepositoryStatusResponse)
@_limiter.limit(_RATE_DEFAULT)
async def get_repository_status(
    request: Request,
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> RepositoryStatusResponse:
    """Return the ingestion status of a repository the caller has access to."""
    repo = get_accessible_repository(repo_id, db, current_user)
    lang_warning = _extract_language_warning(repo.progress_message)
    # Strip |warn= suffix (used internally to store language warnings)
    progress_msg = repo.progress_message
    if lang_warning and progress_msg:
        progress_msg = progress_msg[: progress_msg.find("|warn=")] or None
    # NOTE: |pct= suffix is intentionally kept — the frontend's parseProgress()
    # extracts it to drive the progress bar. Stripping it here broke the UI.
    return RepositoryStatusResponse(
        repo_id=repo.id,
        name=repo.name,
        status=repo.status,
        indexed_at=repo.indexed_at.isoformat() if repo.indexed_at else None,
        progress_message=progress_msg or None,
        language_warning=lang_warning,
    )


# ---------------------------------------------------------------------------
# Access management (owner-only)
# ---------------------------------------------------------------------------

@router.get("/{repo_id}/access", response_model=RepoAccessListResponse)
@_limiter.limit(_RATE_DEFAULT)
async def list_access(
    request: Request,
    repo_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> RepoAccessListResponse:
    """List all users who have access to this repository.  Owner only."""
    require_owner(repo_id, db, current_user)
    grants = db.query(UserRepoModel).filter_by(repo_id=repo_id).all()
    return RepoAccessListResponse(
        repo_id=repo_id,
        grants=[
            AccessGrantResponse(user_id=g.user_id, repo_id=g.repo_id, role=g.role)
            for g in grants
        ],
    )


@router.post("/{repo_id}/access", response_model=AccessGrantResponse, status_code=status.HTTP_201_CREATED)
@_limiter.limit(_RATE_DEFAULT)
async def grant_access(
    request: Request,
    repo_id: str,
    body: AccessGrantRequest,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> AccessGrantResponse:
    """Grant a user access to this repository.  Owner only.

    Valid roles: ``owner``, ``viewer``.
    If the user already has a grant, the role is updated.
    """
    require_owner(repo_id, db, current_user)

    if body.role not in ("owner", "viewer"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Role must be 'owner' or 'viewer'.",
        )

    access = grant_repo_access(body.user_id, repo_id, body.role, db)
    db.commit()
    return AccessGrantResponse(user_id=access.user_id, repo_id=access.repo_id, role=access.role)


# ---------------------------------------------------------------------------
# Entity source code
# ---------------------------------------------------------------------------

class EntitySourceResponse(BaseModel):
    entity_id: str
    name: str
    file_path: str
    start_line: int
    end_line: int
    language: str
    source: str


@router.get("/{repo_id}/entities/{entity_id}/source", response_model=EntitySourceResponse)
@_limiter.limit(_RATE_DEFAULT)
async def get_entity_source(
    request: Request,
    repo_id: str,
    entity_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> EntitySourceResponse:
    """Return the source code stored for a specific entity.

    Used by the frontend citation viewer to show the exact code the LLM
    cited, fetched directly from the database (no disk access required).
    """
    get_accessible_repository(repo_id, db, current_user)

    entity = db.query(EntityModel).filter_by(id=entity_id, repo_id=repo_id).first()
    if not entity:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity '{entity_id}' not found in repository '{repo_id}'.",
        )

    return EntitySourceResponse(
        entity_id=entity.id,
        name=entity.name,
        file_path=entity.file_path,
        start_line=entity.start_line,
        end_line=entity.end_line,
        language=entity.language,
        source=entity.source,
    )


@router.delete("/{repo_id}/access/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
@_limiter.limit(_RATE_DEFAULT)
async def revoke_access(
    request: Request,
    repo_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> None:
    """Revoke a user's access to this repository.  Owner only.

    An owner cannot revoke their own access (prevents orphaned repos).
    """
    require_owner(repo_id, db, current_user)

    if current_user and user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot revoke your own owner access.",
        )

    access = db.query(UserRepoModel).filter_by(user_id=user_id, repo_id=repo_id).first()
    if not access:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No access grant found for user '{user_id}' on repo '{repo_id}'.",
        )
    db.delete(access)
    db.commit()
