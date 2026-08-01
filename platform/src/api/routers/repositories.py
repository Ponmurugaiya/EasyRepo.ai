"""Repository management router for ingestion and metadata endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.api.dependencies import get_db
from src.api.schemas import RepositoryCreateRequest, RepositoryResponse, RepositoryStatusResponse
from src.ingestion.pipeline import ingest_repository
from src.storage.models import EntityModel, RelationshipModel, RepositoryModel

router = APIRouter()


@router.post("", response_model=RepositoryResponse, status_code=status.HTTP_201_CREATED)
async def create_repository(
    request: RepositoryCreateRequest,
    db: Session = Depends(get_db),
) -> RepositoryResponse:
    """Ingest a repository from a local path or Git URL.

    Triggers the full ingestion pipeline (extraction → embedding → storage)
    and returns the resulting repository metadata.
    """
    try:
        repo = ingest_repository(
            repo_path_or_url=request.source,
            db_session=db,
        )
        entity_count = (
            db.query(func.count(EntityModel.id))
            .filter_by(repo_id=repo.id)
            .scalar()
        )
        relationship_count = (
            db.query(func.count(RelationshipModel.id))
            .filter_by(repo_id=repo.id)
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
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Repository ingestion failed: {e}",
        ) from e


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
