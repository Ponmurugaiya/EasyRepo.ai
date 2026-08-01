"""SQLAlchemy ORM models mirroring schema.sql.

Choice of ORM: SQLAlchemy 2.0 with pgvector extension integration.
SQLAlchemy provides type-safe query construction, declarative mapping,
connection pooling, session management, and seamless Alembic migration support.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.embedding.config import EMBEDDING_DIM


class Base(DeclarativeBase):
    """Base class for SQLAlchemy declarative models."""
    pass


class RepositoryModel(Base):
    """ORM model for ``repositories`` table."""

    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    url_or_path: Mapped[str] = mapped_column(Text, nullable=False)
    # Normalised form of url_or_path used for deduplication.
    # UNIQUE constraint ensures two submissions of the same GitHub URL map to
    # one row rather than colliding on the hashed repo_id.
    canonical_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"
    )
    # Human-readable progress message updated at each pipeline stage.
    # Exposed via GET /repositories/{id}/status so the frontend can show
    # live progress during ingestion.
    progress_message: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'indexing', 'ready', 'failed')",
            name="chk_repo_status",
        ),
    )

    # Relationships
    entities: Mapped[List[EntityModel]] = relationship(
        "EntityModel", back_populates="repository", cascade="all, delete-orphan"
    )
    relationships: Mapped[List[RelationshipModel]] = relationship(
        "RelationshipModel", back_populates="repository", cascade="all, delete-orphan"
    )


class EntityModel(Base):
    """ORM model for ``entities`` table."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    repo_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    start_line: Mapped[int] = mapped_column(Integer, nullable=False)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(512), ForeignKey("entities.id", ondelete="SET NULL"), nullable=True
    )
    language: Mapped[str] = mapped_column(String(50), nullable=False)
    has_docstring: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(EMBEDDING_DIM), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    repository: Mapped[RepositoryModel] = relationship(
        "RepositoryModel", back_populates="entities"
    )
    parent: Mapped[Optional[EntityModel]] = relationship(
        "EntityModel", remote_side=[id], backref="children"
    )

    __table_args__ = (
        Index("idx_entities_repo_type", "repo_id", "type"),
        Index("idx_entities_parent_id", "parent_id"),
        Index(
            "idx_entities_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class RelationshipModel(Base):
    """ORM model for ``relationships`` table."""

    __tablename__ = "relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    repo_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[str] = mapped_column(
        String(512), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    target_id: Mapped[Optional[str]] = mapped_column(
        String(512), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True
    )
    external_target_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relationships
    repository: Mapped[RepositoryModel] = relationship(
        "RepositoryModel", back_populates="relationships"
    )
    source_entity: Mapped[EntityModel] = relationship(
        "EntityModel", foreign_keys=[source_id]
    )
    target_entity: Mapped[Optional[EntityModel]] = relationship(
        "EntityModel", foreign_keys=[target_id]
    )

    __table_args__ = (
        CheckConstraint(
            "target_id IS NOT NULL OR external_target_name IS NOT NULL",
            name="chk_rel_target",
        ),
        Index("idx_relationships_repo_source", "repo_id", "source_id"),
        Index("idx_relationships_repo_target_type", "repo_id", "target_id", "type"),
    )


class UserModel(Base):
    """ORM model for ``users`` table.

    A user is identified by an (external_id, provider) pair coming from an
    OAuth provider (GitHub, Google, etc.) or, in dev/test mode, by a
    locally-created account.  ``api_token_hash`` stores the bcrypt hash of
    the user's personal API token — never the token itself.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UUID hex
    external_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="local")
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    api_token_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Back-reference to access grants
    repo_access: Mapped[List["UserRepoModel"]] = relationship(
        "UserRepoModel", back_populates="user", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("external_id", "provider", name="uq_users_external_provider"),
        Index("idx_users_email", "email"),
    )


class UserRepoModel(Base):
    """ORM model for ``user_repos`` join table.

    Governs which users can access which repositories and with what role.

    Roles
    -----
    owner  — can query, trigger re-indexing, grant/revoke viewer access,
             and delete the repository index.
    viewer — can query only.

    The first user to index a repository is automatically granted ``owner``
    role.  Subsequent users who submit the same URL receive ``viewer`` role
    on the shared indexed copy.
    """

    __tablename__ = "user_repos"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    repo_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("repositories.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    user: Mapped[UserModel] = relationship("UserModel", back_populates="repo_access")
    repository: Mapped[RepositoryModel] = relationship("RepositoryModel")

    __table_args__ = (
        CheckConstraint("role IN ('owner', 'viewer')", name="chk_user_repo_role"),
        Index("idx_user_repos_repo_id", "repo_id"),
    )
