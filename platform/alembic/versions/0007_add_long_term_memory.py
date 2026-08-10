"""Add user_memory, user_repo_preferences, and repo_user_memory tables.

Three-tier long-term memory:
  1. user_memory            — global user preferences/background (cross-repo)
  2. user_repo_preferences  — how this user works with a specific repo
  3. repo_user_memory       — facts about a specific repo learned via this user

All tables are append-only from the application's perspective; upserts are
done in Python by checking for exact-match duplicates before inserting.

Revision ID: 0007
Revises:     0006
Create Date: 2026-08-10
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "0007"
down_revision: str = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── user_memory ──────────────────────────────────────────────────────────
    # Global facts about the user — preferences, background, working style.
    # Scoped to user_id only; applies across all repos.
    if "user_memory" not in existing_tables:
        op.create_table(
            "user_memory",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("category", sa.String(64), nullable=False),
            sa.Column("fact", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_user_memory_user_id", "user_memory", ["user_id"])

    # ── user_repo_preferences ────────────────────────────────────────────────
    # How this specific user works with this specific repo — their preferences,
    # habits, and background in the context of this codebase.
    # Scoped to (user_id, repo_id).
    if "user_repo_preferences" not in existing_tables:
        op.create_table(
            "user_repo_preferences",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("repo_id", sa.String(255), nullable=False),
            sa.Column("category", sa.String(64), nullable=False),
            sa.Column("fact", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["repo_id"], ["repositories.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_urp_user_repo",
            "user_repo_preferences",
            ["user_id", "repo_id"],
        )

    # ── repo_user_memory ─────────────────────────────────────────────────────
    # Hard facts about the codebase discovered/confirmed through this user's
    # conversations.  Scoped to (user_id, repo_id).
    if "repo_user_memory" not in existing_tables:
        op.create_table(
            "repo_user_memory",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("repo_id", sa.String(255), nullable=False),
            sa.Column("category", sa.String(64), nullable=False),
            sa.Column("fact", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["repo_id"], ["repositories.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "idx_rum_user_repo",
            "repo_user_memory",
            ["user_id", "repo_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "repo_user_memory" in existing_tables:
        op.drop_index("idx_rum_user_repo", table_name="repo_user_memory")
        op.drop_table("repo_user_memory")

    if "user_repo_preferences" in existing_tables:
        op.drop_index("idx_urp_user_repo", table_name="user_repo_preferences")
        op.drop_table("user_repo_preferences")

    if "user_memory" in existing_tables:
        op.drop_index("idx_user_memory_user_id", table_name="user_memory")
        op.drop_table("user_memory")
