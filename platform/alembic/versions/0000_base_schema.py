"""Create base schema: repositories, entities, relationships tables and indexes.

This is the initial migration — it creates all tables that previously existed
only as `init_db()` / schema.sql DDL outside Alembic.  On a fresh database
this migration creates everything from scratch.  On an existing database that
was set up via `init_db()` the migration will still succeed because Alembic
only tracks which revisions have been applied in the `alembic_version` table.

Revision ID: 0000
Revises:     (none — this is the root migration)
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

# Alembic revision identifiers
revision: str = "0000"
down_revision: str | None = None
branch_labels = None
depends_on = None

# Embedding dimension — must match src/embedding/config.py
EMBEDDING_DIM = 768


def upgrade() -> None:
    # ------------------------------------------------------------------
    # repositories
    # ------------------------------------------------------------------
    op.create_table(
        "repositories",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("url_or_path", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=True, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.CheckConstraint(
            "status IN ('pending', 'indexing', 'ready', 'failed')",
            name="chk_repo_status",
        ),
    )

    # ------------------------------------------------------------------
    # entities
    # ------------------------------------------------------------------
    op.create_table(
        "entities",
        sa.Column("id", sa.String(512), primary_key=True),
        sa.Column(
            "repo_id",
            sa.String(255),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("end_line", sa.Integer(), nullable=False),
        sa.Column(
            "parent_id",
            sa.String(512),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("language", sa.String(50), nullable=False),
        sa.Column("has_docstring", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_entities_repo_type", "entities", ["repo_id", "type"])
    op.create_index("idx_entities_parent_id", "entities", ["parent_id"])
    op.create_index(
        "idx_entities_embedding",
        "entities",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # ------------------------------------------------------------------
    # relationships
    # ------------------------------------------------------------------
    op.create_table(
        "relationships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "repo_id",
            sa.String(255),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.String(512),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            sa.String(512),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("external_target_name", sa.Text(), nullable=True),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "target_id IS NOT NULL OR external_target_name IS NOT NULL",
            name="chk_rel_target",
        ),
    )
    op.create_index(
        "idx_relationships_repo_source", "relationships", ["repo_id", "source_id"]
    )
    op.create_index(
        "idx_relationships_repo_target_type",
        "relationships",
        ["repo_id", "target_id", "type"],
    )

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("external_id", sa.String(255), nullable=True),
        sa.Column("provider", sa.String(50), nullable=False, server_default="local"),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("api_token_hash", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("external_id", "provider", name="uq_users_external_provider"),
    )
    op.create_index("idx_users_email", "users", ["email"])

    # ------------------------------------------------------------------
    # user_repos
    # ------------------------------------------------------------------
    op.create_table(
        "user_repos",
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "repo_id",
            sa.String(255),
            sa.ForeignKey("repositories.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(20), nullable=False, server_default="viewer"),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint("role IN ('owner', 'viewer')", name="chk_user_repo_role"),
    )
    op.create_index("idx_user_repos_repo_id", "user_repos", ["repo_id"])


def downgrade() -> None:
    op.drop_index("idx_user_repos_repo_id", table_name="user_repos")
    op.drop_table("user_repos")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
    op.drop_index("idx_relationships_repo_target_type", table_name="relationships")
    op.drop_index("idx_relationships_repo_source", table_name="relationships")
    op.drop_table("relationships")
    op.drop_index("idx_entities_embedding", table_name="entities")
    op.drop_index("idx_entities_parent_id", table_name="entities")
    op.drop_index("idx_entities_repo_type", table_name="entities")
    op.drop_table("entities")
    op.drop_table("repositories")
