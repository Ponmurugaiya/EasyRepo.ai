"""Add canonical_url column and unique index to repositories table.

Phase 1 of multi-tenancy: stable, collision-resistant repo_id derivation.

NOTE: On a fresh database this migration is a no-op — migration 0000 already
creates the repositories table with the canonical_url column and unique index.
This migration exists for continuity of the revision chain and to correctly
upgrade databases that were bootstrapped before Alembic was introduced.

Revision ID: 0001
Revises:     0000
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

# Alembic revision identifiers
revision: str = "0001"
down_revision: str | None = "0000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("repositories")]

    if "canonical_url" not in columns:
        op.add_column(
            "repositories",
            sa.Column("canonical_url", sa.Text(), nullable=True),
        )
        op.create_index(
            "uq_repositories_canonical_url",
            "repositories",
            ["canonical_url"],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("repositories")]
    if "canonical_url" in columns:
        op.drop_index("uq_repositories_canonical_url", table_name="repositories")
        op.drop_column("repositories", "canonical_url")
