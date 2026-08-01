"""Add users and user_repos tables for Phase 2 multi-tenancy.

users      — identity record (external OAuth ID + provider, email, token hash)
user_repos — join table granting per-user access to shared repository indexes
             with a role (owner | viewer)

Revision ID: 0002
Revises:     0001
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
