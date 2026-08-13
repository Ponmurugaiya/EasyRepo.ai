"""Dependency injection for FastAPI endpoints."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Generator, Optional

import httpx
from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode
from sqlalchemy.orm import Session

from src.storage.db import get_session as get_db_session
from src.storage.models import RepositoryModel, UserModel, UserRepoModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cognito JWT verification
# ---------------------------------------------------------------------------

def _cognito_region() -> str:
    return os.environ.get("COGNITO_REGION", os.environ.get("AWS_REGION", "us-east-1"))

def _cognito_user_pool_id() -> str:
    return os.environ.get("COGNITO_USER_POOL_ID", "")

def _cognito_client_id() -> str:
    return os.environ.get("COGNITO_CLIENT_ID", "")

@lru_cache(maxsize=1)
def _get_jwks(jwks_url: str) -> dict:
    """Fetch and cache Cognito's public JWKS. Cached for the process lifetime."""
    resp = httpx.get(jwks_url, timeout=10)
    resp.raise_for_status()
    return resp.json()

def _verify_cognito_jwt(token: str) -> Optional[dict]:
    """Verify a Cognito JWT and return its claims, or None if invalid."""
    pool_id = _cognito_user_pool_id()
    if not pool_id:
        return None

    region = _cognito_region()
    jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"

    try:
        jwks = _get_jwks(jwks_url)
    except Exception as exc:
        logger.warning("Failed to fetch Cognito JWKS: %s", exc)
        return None

    try:
        # Decode without verification first to extract the key id (kid)
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        # Find the matching public key
        public_key = None
        for key_data in jwks.get("keys", []):
            if key_data.get("kid") == kid:
                public_key = jwk.construct(key_data)
                break

        if public_key is None:
            logger.debug("No matching JWK found for kid=%s", kid)
            return None

        # Verify signature and claims
        client_id = _cognito_client_id()
        issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"

        claims = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            audience=client_id if client_id else None,
            issuer=issuer,
            options={"verify_aud": bool(client_id)},
        )
        return claims
    except JWTError as exc:
        logger.debug("Cognito JWT verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error verifying Cognito JWT: %s", exc)
        return None

def _get_or_create_cognito_user(claims: dict, db: Session) -> UserModel:
    """Return the UserModel for a verified Cognito user, creating one if needed."""
    from src.api.auth import create_user

    # Cognito 'sub' is the stable unique identifier
    external_id = claims.get("sub")
    email = claims.get("email")

    # Try by external_id first, then by email as fallback
    user = None
    if external_id:
        user = db.query(UserModel).filter_by(external_id=external_id, provider="cognito").first()
    if user is None and email:
        user = db.query(UserModel).filter_by(email=email, provider="cognito").first()
        if user and external_id and not user.external_id:
            user.external_id = external_id
            db.flush()

    if user is None:
        # Auto-provision on first login
        user, _ = create_user(db=db, email=email, external_id=external_id, provider="cognito")
        db.commit()
        logger.info("Auto-provisioned Cognito user: sub=%s email=%s", external_id, email)

    return user


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
    authorization: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> Optional[UserModel]:
    """Resolve the calling user from X-API-Key or Authorization: Bearer header.

    Supports two auth methods:
    - X-API-Key: local API token (dev/local usage)
    - Authorization: Bearer <jwt>: Cognito JWT (Google sign-in / production)

    When AUTH_ENABLED=true: a valid credential is required — raises 401 if
    missing or invalid.

    When AUTH_ENABLED=false (default dev mode): auth is optional. If a
    credential is present it is resolved to a user; if absent the request
    proceeds as anonymous (None).
    """
    auth_required = _auth_enabled()

    # ── Try Cognito JWT from Authorization: Bearer header ─────────────────
    if authorization and authorization.lower().startswith("bearer "):
        jwt_token = authorization[7:].strip()
        claims = _verify_cognito_jwt(jwt_token)
        if claims:
            return _get_or_create_cognito_user(claims, db)
        # Invalid JWT
        if auth_required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired Cognito token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return None

    # ── Try local API token from X-API-Key header ─────────────────────────
    if x_api_key:
        from src.api.auth import lookup_user_by_token
        user = lookup_user_by_token(x_api_key, db)
        if user:
            return user
        if auth_required:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired API token.",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return None

    # ── No credential provided ────────────────────────────────────────────
    if auth_required:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return None


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
    - Auth enabled / optional-auth with a resolved user:
        * If the user already has a user_repos row → allow.
        * If the repo exists and is ``ready`` → auto-grant viewer access so
          that a dev user whose token was added after the repo was first indexed
          (when they were anonymous) never gets a hard 403.  This matches the
          "shared index" behaviour documented in the ingest endpoint.
        * If the repo is not ready and the user has no grant → 403.

    Raises:
    - 404 if the repository does not exist.
    - 403 if it exists, is not yet ready, and the user has no access record.
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
    if access:
        return repo

    # No grant yet — auto-grant viewer on ready repos (shared-index convenience)
    if repo.status == "ready":
        grant_repo_access(current_user.id, repo_id, "viewer", db)
        try:
            db.commit()
        except Exception:
            db.rollback()
        return repo

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"You do not have access to repository '{repo_id}'.",
    )


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
