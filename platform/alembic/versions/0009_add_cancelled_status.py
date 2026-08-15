"""Add 'cancelled' to repositories.status check constraint.

Revision ID: 0009
Revises:     0008
Create Date: 2026-08-15
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect as sa_inspect

revision: str = "0009"
down_revision: str = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "repositories" not in inspector.get_table_names():
        return  # fresh DB — models.py creates the constraint correctly

    # Drop the old check constraint and recreate with 'cancelled' added.
    op.drop_constraint("chk_repo_status", "repositories", type_="check")
    op.create_check_constraint(
        "chk_repo_status",
        "repositories",
        "status IN ('pending', 'indexing', 'ready', 'failed', 'cancelled')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa_inspect(bind)
    if "repositories" not in inspector.get_table_names():
        return
    op.drop_constraint("chk_repo_status", "repositories", type_="check")
    op.create_check_constraint(
        "chk_repo_status",
        "repositories",
        "status IN ('pending', 'indexing', 'ready', 'failed')",
    )
