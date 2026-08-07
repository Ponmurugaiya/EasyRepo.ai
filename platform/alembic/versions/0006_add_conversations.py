"""Add conversations and conversation_turns tables for conversation history.

Authenticated users get their turns persisted in the DB (with rolling LLM
summaries on older turns).  Anonymous users rely on the frontend sending
history in the request body — nothing is written to these tables for them.

Revision ID: 0006
Revises:     0005
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "0006"
down_revision: str = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    # ── conversations ────────────────────────────────────────────────────────
    if "conversations" not in existing_tables:
        op.create_table(
            "conversations",
            sa.Column("id", sa.String(128), nullable=False),
            sa.Column("user_id", sa.String(64), nullable=True),
            sa.Column("repo_id", sa.String(255), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("summarized_through_turn", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["repo_id"],
                ["repositories.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("idx_conversations_user_repo", "conversations", ["user_id", "repo_id"])

    # ── conversation_turns ───────────────────────────────────────────────────
    if "conversation_turns" not in existing_tables:
        op.create_table(
            "conversation_turns",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("conversation_id", sa.String(128), nullable=False),
            sa.Column("turn_index", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["conversation_id"],
                ["conversations.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("role IN ('user', 'assistant')", name="chk_turn_role"),
        )
        op.create_index(
            "idx_turns_conv_index",
            "conversation_turns",
            ["conversation_id", "turn_index"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "conversation_turns" in existing_tables:
        op.drop_index("idx_turns_conv_index", table_name="conversation_turns")
        op.drop_table("conversation_turns")

    if "conversations" in existing_tables:
        op.drop_index("idx_conversations_user_repo", table_name="conversations")
        op.drop_table("conversations")
