"""User authentication and token utilities.

Token design
------------
Personal API tokens are opaque random strings — ``{user_id}.{secret}`` where:
  - ``user_id`` is the 16-char hex prefix of the user's UUID (for fast DB lookup)
  - ``secret``  is 32 bytes of URL-safe base64-encoded random data

The full token is shown to the user exactly once (on creation or rotation).
Only the bcrypt hash of the *full token* is stored in ``users.api_token_hash``.

Verification is intentionally slightly expensive (bcrypt) to resist offline
dictionary attacks on a compromised DB dump.  For high-throughput scenarios
add a Redis token cache keyed by SHA-256(token) → user_id with a short TTL.

Token format example:
    er_112559936432be48.aBcDeFgHiJkLmNoPqRsTuVwXyZ01234567890abc
    └────── prefix ──┘  └──────────── secret (44 chars) ──────────┘
"""

from __future__ import annotations

import secrets
import uuid
import warnings
from typing import Optional

from passlib.context import CryptContext
from sqlalchemy.orm import Session

from src.storage.models import UserModel

# ---------------------------------------------------------------------------
# Password context — bcrypt with a cost factor appropriate for token hashing.
# rounds=12 is the passlib default; fine for personal tokens verified once
# per request (or cached).
# ---------------------------------------------------------------------------
# Silence passlib's version-sniffing warning against bcrypt 4.x.
# The underlying bcrypt operations work correctly — passlib just can't read
# the __version__ attribute that bcrypt 4.x removed.
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*error reading bcrypt version.*")
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

_TOKEN_PREFIX = "er_"


def _new_user_id() -> str:
    """Generate a new UUID-based user ID (32 hex chars, no hyphens)."""
    return uuid.uuid4().hex


def generate_token(user_id: str) -> tuple[str, str]:
    """Generate a new personal API token for *user_id*.

    Returns ``(plaintext_token, hash)`` where only the hash should be stored.
    The plaintext token is shown to the user once and then discarded.
    """
    secret = secrets.token_urlsafe(32)
    # Embed a 16-char prefix of the user_id so verification can do a DB lookup
    # without scanning the whole table.
    prefix = user_id[:16]
    plaintext = f"{_TOKEN_PREFIX}{prefix}.{secret}"
    token_hash = _pwd_ctx.hash(plaintext)
    return plaintext, token_hash


def verify_token(plaintext: str, token_hash: str) -> bool:
    """Return True if *plaintext* matches *token_hash*."""
    try:
        return _pwd_ctx.verify(plaintext, token_hash)
    except Exception:
        return False


def extract_user_id_prefix(token: str) -> Optional[str]:
    """Extract the user_id prefix from a token string for fast DB lookup.

    Returns None if the token is malformed.
    """
    # Format: er_{16-char-prefix}.{secret}
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
    """Create a new user and return ``(user, plaintext_token)``.

    The plaintext token is returned once for the caller to relay to the user.
    Only the hash is persisted.
    """
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
    db.flush()  # get the row into the session without committing
    return user, plaintext


def rotate_token(user: UserModel, db: Session) -> str:
    """Issue a new token for *user*, invalidating the previous one.

    Returns the plaintext token (shown once, then discarded).
    """
    plaintext, token_hash = generate_token(user.id)
    user.api_token_hash = token_hash
    db.flush()
    return plaintext


def lookup_user_by_token(token: str, db: Session) -> Optional[UserModel]:
    """Return the ``UserModel`` whose token matches, or None.

    Uses the embedded prefix for a targeted DB query, then bcrypt-verifies
    the full token against the stored hash.
    """
    prefix = extract_user_id_prefix(token)
    if not prefix:
        return None

    # Find candidates whose id starts with the prefix
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
