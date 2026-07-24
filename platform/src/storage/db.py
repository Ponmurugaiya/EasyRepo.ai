"""Database connection and session management for PostgreSQL + pgvector.

Uses SQLAlchemy 2.0 ``create_engine`` and ``sessionmaker``.
Includes schema initialization logic that enables pgvector extension and creates
all tables if they do not exist.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

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


def get_engine(db_url: str = DEFAULT_DB_URL) -> Engine:
    """Get or create the singleton SQLAlchemy Engine instance."""
    global _engine
    if _engine is None or str(_engine.url) != db_url:
        _engine = create_engine(
            db_url,
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

    Executes raw SQL DDL `CREATE EXTENSION IF NOT EXISTS vector` first,
    then creates all tables declared in ``src.storage.models.Base``.
    """
    engine = get_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    Base.metadata.create_all(bind=engine)
