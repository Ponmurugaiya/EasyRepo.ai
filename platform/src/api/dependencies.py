"""Dependency injection for FastAPI endpoints."""

from __future__ import annotations

import os
from typing import Generator

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.storage.db import get_session as get_db_session
from src.storage.models import RepositoryModel


# ---------------------------------------------------------------------------
# Database URL
# ---------------------------------------------------------------------------


def get_db_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
    )


# ---------------------------------------------------------------------------
# Database session
# ---------------------------------------------------------------------------


def get_db(db_url: str = Depends(get_db_url)) -> Generator[Session, None, None]:
    with get_db_session(db_url) as session:
        yield session


# ---------------------------------------------------------------------------
# LLM API keys
# ---------------------------------------------------------------------------


def get_gemini_api_key() -> str:
    """Resolve the Gemini API key.

    Returns an empty string when the key is absent — the LiteLLM router
    treats that as "skip Gemini" and falls back to whatever other provider
    has a key.  Only raises if ``EASYREPO_MOCK_GEMINI`` is set (test mode).

    The old behaviour (raise 502 when key missing) was correct when Gemini
    was the sole provider.  Now that Groq is primary this would block the
    request unnecessarily, so we degrade gracefully instead.
    """
    if os.environ.get("EASYREPO_MOCK_GEMINI", "").strip().lower() in {"true", "1", "yes", "on"}:
        return "mock-gemini-key"
    return os.environ.get("GEMINI_API_KEY", "")


# ---------------------------------------------------------------------------
# Repository lookup helper
# ---------------------------------------------------------------------------


def get_repository(repo_id: str, db: Session = Depends(get_db)) -> RepositoryModel:
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository with id '{repo_id}' not found",
        )
    return repo
