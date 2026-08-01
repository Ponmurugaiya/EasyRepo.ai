"""Dependency injection for FastAPI endpoints."""

from __future__ import annotations

import os
from typing import Generator, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.storage.db import get_session as get_db_session
from src.storage.models import RepositoryModel, UserModel, UserRepoModel


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
# User authentication
#
# When AUTH_ENABLED=true (or any truthy value), every protected endpoint
# requires a valid personal API token in the ``X-API-Key`` header.
# The token is resolved to a UserModel via lookup_user_by_token().
#
# When AUTH_ENABLED is unset or falsy the dependency returns None, meaning
# all endpoints behave as if called by an anonymous (un-owned) user.
# Existing dev/test workflows require no config changes.
#
# To enable auth:  AUTH_ENABLED=true in .env
# ---------------------------------------------------------------------------

_AUTH_ENABLED_ENV = "AUTH_ENABLED"


def _auth_enabled() -> bool:
    return os.environ.get(_AUTH_ENABLED_ENV, "").strip().lower() in {
        "true", "1", "yes", "on"
    }


def get_current_user(
    x_api_key: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Optional[UserModel]:
    """Resolve the calling user from the X-API-Key header.

    Returns a ``UserModel`` when auth is enabled and the token is valid.
    Returns ``None`` when auth is disabled (dev / local mode).
    Raises 401 when auth is enabled and the token is missing or invalid.
    """
    if not _auth_enabled():
        return None

    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    from src.api.auth import lookup_user_by_token
    user = lookup_user_by_token(x_api_key, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired API token.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return user


def verify_api_key(
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> None:
    """Compatibility shim — kept for use as a router-level dependency.

    The real auth work is done by ``get_current_user``; this dependency exists
    so existing ``dependencies=[Depends(verify_api_key)]`` calls continue to
    work without change.  It intentionally returns nothing; use
    ``get_current_user`` directly when you need the UserModel in a handler.
    """
    pass  # get_current_user already raised 401 if auth failed


# ---------------------------------------------------------------------------
# LLM API keys
# ---------------------------------------------------------------------------


def get_gemini_api_key() -> str:
    """Resolve the Gemini API key.

    Returns an empty string when the key is absent — the LiteLLM router
    treats that as "skip Gemini" and falls back to whatever other provider
    has a key.  Only raises if ``EASYREPO_MOCK_GEMINI`` is set (test mode).
    """
    if os.environ.get("EASYREPO_MOCK_GEMINI", "").strip().lower() in {"true", "1", "yes", "on"}:
        return "mock-gemini-key"
    return os.environ.get("GEMINI_API_KEY", "")


# ---------------------------------------------------------------------------
# Repository access helpers
# ---------------------------------------------------------------------------


def get_repository(repo_id: str, db: Session = Depends(get_db)) -> RepositoryModel:
    """Return a repository by ID, raising 404 if not found.

    Does NOT enforce per-user access control — used only for internal lookups
    (e.g. health checks, ingestion pipeline).  Use ``get_accessible_repository``
    in user-facing endpoints.
    """
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found.",
        )
    return repo


def get_accessible_repository(
    repo_id: str,
    db: Session,
    current_user: Optional[UserModel],
) -> RepositoryModel:
    """Return a repository only if the calling user has access to it.

    Rules:
    - Auth disabled (current_user is None): any existing repo is accessible.
    - Auth enabled: the user must have a ``user_repos`` row for this repo.

    Raises:
    - 404 if the repository does not exist.
    - 403 if it exists but the user has no access record.
    """
    repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
    if not repo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Repository '{repo_id}' not found.",
        )

    if current_user is None:
        # Auth disabled — open access
        return repo

    access = (
        db.query(UserRepoModel)
        .filter_by(user_id=current_user.id, repo_id=repo_id)
        .first()
    )
    if not access:
        # Repo exists but this user has no grant — return 403, not 404,
        # because 404 would hint that the repo doesn't exist at all.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You do not have access to repository '{repo_id}'.",
        )
    return repo


def require_owner(
    repo_id: str,
    db: Session,
    current_user: Optional[UserModel],
) -> UserRepoModel:
    """Assert the calling user is an owner of the repository.

    Returns the ``UserRepoModel`` access record on success.
    Raises 403 if the user is a viewer, 404 if the repo or grant is missing.
    Auth-disabled mode grants implicit owner rights.
    """
    # First ensure the repo exists and the user can see it
    get_accessible_repository(repo_id, db, current_user)

    if current_user is None:
        # Auth disabled — implicit owner
        # Return a synthetic record so callers don't need to branch
        return UserRepoModel(user_id="anonymous", repo_id=repo_id, role="owner")

    access = (
        db.query(UserRepoModel)
        .filter_by(user_id=current_user.id, repo_id=repo_id)
        .first()
    )
    if not access or access.role != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the repository owner can perform this action.",
        )
    return access


def grant_repo_access(
    user_id: str,
    repo_id: str,
    role: str,
    db: Session,
) -> UserRepoModel:
    """Grant *role* on *repo_id* to *user_id*, upserting if already exists."""
    access = db.query(UserRepoModel).filter_by(user_id=user_id, repo_id=repo_id).first()
    if access:
        access.role = role
    else:
        from datetime import datetime, timezone
        access = UserRepoModel(
            user_id=user_id,
            repo_id=repo_id,
            role=role,
        )
        db.add(access)
    db.flush()
    return access
