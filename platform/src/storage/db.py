"""Database connection and session management for PostgreSQL + pgvector.

Uses SQLAlchemy 2.0 ``create_engine`` and ``sessionmaker``.
Includes schema initialization logic that enables pgvector extension and creates
all tables if they do not exist.

Supabase / remote Postgres notes
---------------------------------
When ``DATABASE_URL`` points to a remote host (anything other than localhost /
127.0.0.1), ``sslmode=require`` is added automatically so connections are
always encrypted.  Local Docker connections are unaffected.

``make_psycopg_dsn()`` converts the SQLAlchemy-style URL into a psycopg v3
compatible DSN string, which is required by the Procrastinate
``PsycopgConnector``.  SQLAlchemy uses ``postgresql+psycopg2://`` or plain
``postgresql://`` schemes; psycopg v3 expects ``postgresql://`` with no driver
suffix.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.storage.models import Base

DEFAULT_DB_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo"
)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _is_remote(db_url: str) -> bool:
    """Return True if the URL points to a host other than localhost."""
    parsed = urlparse(db_url)
    host = (parsed.hostname or "").lower()
    return host not in ("localhost", "127.0.0.1", "::1", "")


def _add_ssl_if_remote(db_url: str) -> str:
    """Add ``sslmode=require`` to the URL when connecting to a remote host.

    Safe to call multiple times — will not add a duplicate sslmode if one is
    already present.
    """
    if not _is_remote(db_url):
        return db_url

    parsed = urlparse(db_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if "sslmode" not in params:
        params["sslmode"] = ["require"]
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


def make_psycopg_dsn(db_url: str = DEFAULT_DB_URL) -> str:
    """Return a psycopg v3 compatible DSN derived from *db_url*.

    Procrastinate's ``PsycopgConnector`` requires a plain ``postgresql://``
    connection string (no SQLAlchemy driver suffix such as ``+psycopg2`` or
    ``+psycopg``).  SSL is added automatically for remote hosts.

    Args:
        db_url: A SQLAlchemy-style database URL.

    Returns:
        A ``postgresql://`` URL suitable for ``psycopg.AsyncConnection.connect``.
    """
    # Strip any driver suffix (e.g. postgresql+psycopg2 → postgresql)
    url = db_url
    if url.startswith("postgresql+"):
        url = "postgresql" + url[url.index("://"):]
    elif url.startswith("postgres://"):
        # Some providers (Heroku, Supabase) use postgres:// — psycopg3 accepts it
        pass

    return _add_ssl_if_remote(url)


def _sqlalchemy_url(db_url: str) -> str:
    """Return the URL ready for SQLAlchemy, with SSL connect_args handled separately.

    SQLAlchemy passes SSL via ``connect_args`` rather than URL params for some
    drivers, but ``sslmode`` in the URL query string is also honoured by
    psycopg2 and psycopg3 — so we just ensure it's present in the URL.
    """
    return _add_ssl_if_remote(db_url)


def get_engine(db_url: str = DEFAULT_DB_URL) -> Engine:
    """Get or create the singleton SQLAlchemy Engine instance."""
    global _engine
    sa_url = _sqlalchemy_url(db_url)
    if _engine is None or str(_engine.url) != sa_url:
        _engine = create_engine(
            sa_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory(db_url: str = DEFAULT_DB_URL) -> sessionmaker[Session]:
    """Get or create the sessionmaker factory for DB sessions."""
    global _SessionFactory
    engine = get_engine(db_url)
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return _SessionFactory


@contextmanager
def get_session(db_url: str = DEFAULT_DB_URL) -> Generator[Session, None, None]:
    """Provide a transactional database session context."""
    factory = get_session_factory(db_url)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(db_url: str = DEFAULT_DB_URL) -> None:
    """Initialize database by creating pgvector extension and ORM tables.

    Executes raw SQL DDL ``CREATE EXTENSION IF NOT EXISTS vector`` first,
    then creates all tables declared in ``src.storage.models.Base``.
    """
    engine = get_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=engine)
