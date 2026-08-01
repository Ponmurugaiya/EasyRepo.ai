"""Add progress_message column to repositories table.

Used by the ingestion pipeline to write human-readable stage updates that
the frontend polls via GET /repositories/{id}/status.

Revision ID: 0004
Revises:     0003
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "0004"
down_revision: str = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "repositories" not in existing_tables:
        return  # fresh DB — models.py creates the column correctly

    # Check if column already exists
    cols = [c["name"] for c in inspector.get_columns("repositories")]
    if "progress_message" not in cols:
        op.add_column(
            "repositories",
            sa.Column("progress_message", sa.String(255), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    cols = [c["name"] for c in inspector.get_columns("repositories")]
    if "progress_message" in cols:
        op.drop_column("repositories", "progress_message")
