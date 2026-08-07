"""User authentication and token utilities.

Token design
------------
Personal API tokens are opaque random strings — ``{user_id}.{secret}`` where:
  - ``user_id`` is the 16-char hex prefix of the user's UUID (for fast DB lookup)
  - ``secret``  is 32 bytes of URL-safe base64-encoded random data

The full token is shown to the user exactly once (on creation or rotation).
Only the HMAC-SHA256 hash of the full token is stored in
``users.api_token_hash``, encoded as a hex digest prefixed with "sha256:".

HMAC-SHA256 is the industry standard for API token storage (GitHub, Stripe,
etc. all use it).  The token itself already contains 32 bytes (256 bits) of
cryptographic randomness, so bcrypt's key-stretching adds no meaningful
security benefit — constant-time HMAC comparison is sufficient.

Token format example:
    er_112559936432be48.aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567890abc
    └────── prefix ──┘  └──────────── secret (44 chars) ──────────┘
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from typing import Optional

from sqlalchemy.orm import Session

from src.storage.models import UserModel

_TOKEN_PREFIX = "er_"

# HMAC key — read from env so the same tokens survive process restarts.
# Falls back to a stable default for pure-local dev (single process).
# In production set TOKEN_HMAC_KEY to a long random secret in .env.
_HMAC_KEY: bytes = os.environ.get("TOKEN_HMAC_KEY", "easyrepo-dev-hmac-key").encode()


# ---------------------------------------------------------------------------
# Token hashing — HMAC-SHA256
# ---------------------------------------------------------------------------

def _hash_token(plaintext: str) -> str:
    """Return ``sha256:<hex>`` for the given plaintext token."""
    digest = hmac.new(_HMAC_KEY, plaintext.encode(), hashlib.sha256).hexdigest()
    return f"sha256:{digest}"


def _verify_token_hash(plaintext: str, stored_hash: str) -> bool:
    """Constant-time comparison of *plaintext* against *stored_hash*."""
    if not stored_hash.startswith("sha256:"):
        return False
    expected = _hash_token(plaintext)
    return hmac.compare_digest(expected, stored_hash)


# ---------------------------------------------------------------------------
# Public helpers (same interface as before — callers are unchanged)
# ---------------------------------------------------------------------------

def _new_user_id() -> str:
    """Generate a new UUID-based user ID (32 hex chars, no hyphens)."""
    return uuid.uuid4().hex


def generate_token(user_id: str) -> tuple[str, str]:
    """Generate a new personal API token for *user_id*.

    Returns ``(plaintext_token, hash)`` where only the hash should be stored.
    The plaintext token is shown to the user once and then discarded.
    """
    secret = secrets.token_urlsafe(32)
    prefix = user_id[:16]
    plaintext = f"{_TOKEN_PREFIX}{prefix}.{secret}"
    token_hash = _hash_token(plaintext)
    return plaintext, token_hash


def verify_token(plaintext: str, token_hash: str) -> bool:
    """Return True if *plaintext* matches *token_hash*."""
    return _verify_token_hash(plaintext, token_hash)


def extract_user_id_prefix(token: str) -> Optional[str]:
    """Extract the user_id prefix from a token string for fast DB lookup.

    Returns None if the token is malformed.
    """
    if not token.startswith(_TOKEN_PREFIX):
        return None
    rest = token[len(_TOKEN_PREFIX):]
    dot_pos = rest.find(".")
    if dot_pos != 16:
        return None
    return rest[:16]


# ---------------------------------------------------------------------------
# User CRUD helpers used by the auth router and dependencies
# ---------------------------------------------------------------------------

def create_user(
    db: Session,
    email: Optional[str] = None,
    external_id: Optional[str] = None,
    provider: str = "local",
) -> tuple[UserModel, str]:
    """Create a new user and return ``(user, plaintext_token)``."""
    user_id = _new_user_id()
    plaintext, token_hash = generate_token(user_id)

    user = UserModel(
        id=user_id,
        external_id=external_id,
        provider=provider,
        email=email,
        api_token_hash=token_hash,
    )
    db.add(user)
    db.flush()
    return user, plaintext


def rotate_token(user: UserModel, db: Session) -> str:
    """Issue a new token for *user*, invalidating the previous one."""
    plaintext, token_hash = generate_token(user.id)
    user.api_token_hash = token_hash
    db.flush()
    return plaintext


def lookup_user_by_token(token: str, db: Session) -> Optional[UserModel]:
    """Return the ``UserModel`` whose token matches, or None."""
    prefix = extract_user_id_prefix(token)
    if not prefix:
        return None

    candidates = (
        db.query(UserModel)
        .filter(UserModel.id.like(f"{prefix}%"))
        .filter(UserModel.api_token_hash.isnot(None))
        .all()
    )
    for user in candidates:
        if verify_token(token, user.api_token_hash):  # type: ignore[arg-type]
            return user
    return None
