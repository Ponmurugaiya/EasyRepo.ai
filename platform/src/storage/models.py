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
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="pending"
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
