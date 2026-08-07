"""Add conversation_memory table for Long-Term Memory (LTM).

LTM stores structured knowledge produced by the Answer Agent and keyed by
(repo_id, session_id, feature_name).  Entries are invalidated when the
repository is re-indexed (repo_indexed_at mismatch).

Revision ID: 0005
Revises:     0004
Create Date: 2026-08-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "0005"
down_revision: str = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "conversation_memory" in existing_tables:
        return  # already migrated

    op.create_table(
        "conversation_memory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("repo_id", sa.String(255), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("feature_name", sa.String(255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_entity_ids", sa.JSON(), nullable=True),
        sa.Column("graph_paths", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("exploration_status", sa.String(20), nullable=False, server_default="partial"),
        sa.Column("repo_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repositories.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="chk_ltm_confidence",
        ),
        sa.CheckConstraint(
            "exploration_status IN ('partial', 'complete')",
            name="chk_ltm_exploration_status",
        ),
    )
    op.create_index("idx_ltm_repo_session", "conversation_memory", ["repo_id", "session_id"])
    op.create_index(
        "idx_ltm_repo_session_feature",
        "conversation_memory",
        ["repo_id", "session_id", "feature_name"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "conversation_memory" not in existing_tables:
        return

    op.drop_index("idx_ltm_repo_session_feature", table_name="conversation_memory")
    op.drop_index("idx_ltm_repo_session", table_name="conversation_memory")
    op.drop_table("conversation_memory")
