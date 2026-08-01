"""Authentication router — user registration and token management.

Endpoints
---------
POST /auth/register        Create a new local user account and return a token.
POST /auth/token/rotate    Rotate the calling user's API token.
GET  /auth/me              Return the calling user's profile.

These endpoints are intentionally minimal — they support local/dev usage and
provide the foundation for plugging in an OAuth flow later (GitHub, Google).
In a production OAuth setup, ``POST /auth/register`` would be replaced by the
OAuth callback that creates-or-updates the user record and issues a token.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.auth import create_user, rotate_token
from src.api.dependencies import get_current_user, get_db
from src.storage.models import UserModel

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas (local to this router — small enough to not warrant a shared file)
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: str = Field(..., description="Email address for the new account", max_length=255)


class TokenResponse(BaseModel):
    """Returned once on registration or rotation.  The token is not recoverable."""
    token: str
    user_id: str
    note: str = "Store this token securely — it will not be shown again."


class UserProfileResponse(BaseModel):
    user_id: str
    email: str | None
    provider: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Create a new local user account and return a personal API token.

    The token is shown exactly once.  Store it — there is no recovery path
    (use ``POST /auth/token/rotate`` to issue a new one if lost).

    In a production OAuth setup this endpoint is replaced by the OAuth
    callback handler; the schema and token format remain the same.
    """
    # Prevent duplicate email registrations
    existing = db.query(UserModel).filter_by(email=body.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account with email '{body.email}' already exists.",
        )

    user, plaintext = create_user(db=db, email=body.email, provider="local")
    db.commit()

    return TokenResponse(token=plaintext, user_id=user.id)


@router.post("/token/rotate", response_model=TokenResponse)
async def rotate(
    current_user: UserModel = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TokenResponse:
    """Invalidate the current token and issue a new one.

    The new token is shown exactly once.
    Requires a valid X-API-Key header (the current token).
    """
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Auth is not enabled. Set AUTH_ENABLED=true to use token management.",
        )
    plaintext = rotate_token(current_user, db)
    db.commit()
    return TokenResponse(token=plaintext, user_id=current_user.id)


@router.get("/me", response_model=UserProfileResponse)
async def me(
    current_user: UserModel | None = Depends(get_current_user),
) -> UserProfileResponse:
    """Return the calling user's profile.

    Returns a placeholder when auth is disabled (AUTH_ENABLED not set).
    """
    if current_user is None:
        return UserProfileResponse(
            user_id="anonymous",
            email=None,
            provider="none (auth disabled)",
        )
    return UserProfileResponse(
        user_id=current_user.id,
        email=current_user.email,
        provider=current_user.provider,
    )
