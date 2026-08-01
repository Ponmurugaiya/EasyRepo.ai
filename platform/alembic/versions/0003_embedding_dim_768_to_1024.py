"""Migrate embedding dimension from 768 (jina-v2-base-code) to 1024 (voyage-code-3).

This migration:
1. Drops the HNSW index on the embedding column (required before altering type)
2. Alters the vector(768) column to vector(1024)
3. Re-creates the HNSW index

All existing embeddings are wiped because they were generated with a different
model — they are incompatible with voyage-code-3 vectors and would produce
meaningless similarity scores. Any repo with status='ready' is reset to
'pending' so it will be re-indexed on next submission.

Revision ID: 0003
Revises:     0002
Create Date: 2026-08-01
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

revision: str = "0003"
down_revision: str = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "entities" not in existing_tables:
        # Fresh database — schema.sql will create the column with the right size
        return

    # 1. Drop the HNSW index (cannot alter column type while index exists)
    op.execute("DROP INDEX IF EXISTS idx_entities_embedding")

    # 2. Wipe existing embeddings — they are incompatible with the new model
    op.execute("UPDATE entities SET embedding = NULL")

    # 3. Alter column type from vector(768) to vector(1024)
    op.execute("ALTER TABLE entities ALTER COLUMN embedding TYPE vector(1024)")

    # 4. Reset any ready repos to pending so they get re-indexed
    op.execute("UPDATE repositories SET status = 'pending', indexed_at = NULL WHERE status = 'ready'")

    # 5. Re-create the HNSW index for the new dimension
    op.execute(
        "CREATE INDEX idx_entities_embedding ON entities "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    existing_tables = inspector.get_table_names()

    if "entities" not in existing_tables:
        return

    op.execute("DROP INDEX IF EXISTS idx_entities_embedding")
    op.execute("UPDATE entities SET embedding = NULL")
    op.execute("ALTER TABLE entities ALTER COLUMN embedding TYPE vector(768)")
    op.execute(
        "CREATE INDEX idx_entities_embedding ON entities "
        "USING hnsw (embedding vector_cosine_ops)"
    )
