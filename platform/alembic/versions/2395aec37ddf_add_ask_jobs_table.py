"""add_ask_jobs_table

Revision ID: 2395aec37ddf
Revises: 0007
Create Date: 2026-08-14 18:43:43.986332

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2395aec37ddf'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'ask_jobs',
        sa.Column('id', sa.String(128), primary_key=True),
        sa.Column('repo_id', sa.String(255), sa.ForeignKey('repositories.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(64), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed')",
            name='chk_ask_job_status',
        ),
    )
    op.create_index('idx_ask_jobs_repo_user', 'ask_jobs', ['repo_id', 'user_id'])
    op.create_index('idx_ask_jobs_status', 'ask_jobs', ['status'])


def downgrade() -> None:
    op.drop_index('idx_ask_jobs_status', table_name='ask_jobs')
    op.drop_index('idx_ask_jobs_repo_user', table_name='ask_jobs')
    op.drop_table('ask_jobs')
