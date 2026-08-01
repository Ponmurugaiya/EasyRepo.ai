"""Add canonical_url column and unique index to repositories table.

Phase 1 of multi-tenancy: stable, collision-resistant repo_id derivation.

Previously repo_id was derived from the folder name, causing two separate
repos named "my-project" to overwrite each other.  Now repo_id is a SHA-256
hash of the canonical URL/path, and canonical_url stores the normalised form
for deduplication.

Revision ID: 0001
Revises:     (initial)
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# Alembic revision identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the canonical_url column — nullable so existing rows are not broken.
    # Existing rows will have canonical_url = NULL until they are re-ingested.
    op.add_column(
        "repositories",
        sa.Column("canonical_url", sa.Text(), nullable=True),
    )
    # Unique index — enforces one indexed copy per unique source URL/path.
    op.create_index(
        "uq_repositories_canonical_url",
        "repositories",
        ["canonical_url"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_repositories_canonical_url", table_name="repositories")
    op.drop_column("repositories", "canonical_url")
