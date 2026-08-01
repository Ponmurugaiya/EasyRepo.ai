"""Repository management router for ingestion and metadata endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_db_url
from src.api.schemas import RepositoryCreateRequest, RepositoryResponse, RepositoryStatusResponse
from src.jobs.queue import ingest_repo_task
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel

router = APIRouter()


@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_repository(
    request: RepositoryCreateRequest,
    db: Session = Depends(get_db),
    db_url: str = Depends(get_db_url),
) -> RepositoryResponse:
    """Enqueue a repository for background ingestion via Procrastinate.

    Returns ``202 Accepted`` immediately with ``status: "pending"``.
    The job is persisted in Postgres — it survives API restarts.
    Poll ``GET /repositories/{id}/status`` until status is ``"ready"``
    or ``"failed"``.
    """
    repo_path = Path(request.source).resolve()
    repo_name = repo_path.name
    repo_id = repo_path.name.lower().replace(" ", "-")

    # Upsert the repository row so the caller gets a repo_id right away
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if repo:
        # Re-ingestion: reset to pending; the worker will clear old data
        repo.status = "pending"
        repo.indexed_at = None
    else:
        repo = RepositoryModel(
            id=repo_id,
            url_or_path=str(repo_path),
            name=repo_name,
            status="pending",
        )
        db.add(repo)
    db.commit()

    # Persist the job in Postgres via Procrastinate — durable across restarts
    await ingest_repo_task.defer_async(
        repo_path_or_url=request.source,
        db_url=db_url,
        repo_id=repo_id,
        repo_name=repo_name,
    )

    return RepositoryResponse(
        repo_id=repo.id,
        name=repo.name,
        status=repo.status,
        url_or_path=repo.url_or_path,
        entity_count=0,
        relationship_count=0,
        indexed_at=None,
    )


@router.get("/{repo_id}", response_model=RepositoryResponse)
async def get_repository(
    repo_id: str,
    db: Session = Depends(get_db),
) -> RepositoryResponse:
    """Return metadata for a previously ingested repository."""
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id '{repo_id}' not found",
        )
    entity_count = (
        db.query(func.count(EntityModel.id))
        .filter_by(repo_id=repo_id)
        .scalar()
    )
    relationship_count = (
        db.query(func.count(RelationshipModel.id))
        .filter_by(repo_id=repo_id)
        .scalar()
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


@router.get("/{repo_id}/status", response_model=RepositoryStatusResponse)
async def get_repository_status(
    repo_id: str,
    db: Session = Depends(get_db),
) -> RepositoryStatusResponse:
    """Return the ingestion status of a repository."""
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id '{repo_id}' not found",
        )
    return RepositoryStatusResponse(
        repo_id=repo.id,
        name=repo.name,
        status=repo.status,
        indexed_at=repo.indexed_at.isoformat() if repo.indexed_at else None,
    )
