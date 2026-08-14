"""add_ask_job_progress

Revision ID: 0008
Revises: 2395aec37ddf
Create Date: 2026-08-14

Adds a ``progress`` JSON column to ``ask_jobs`` so the worker can write
live pipeline stage updates while the job is running.  The frontend polls
this field to show accurate per-pipeline stage indicators.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0008'
down_revision: Union[str, None] = '2395aec37ddf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ask_jobs',
        sa.Column('progress', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('ask_jobs', 'progress')
