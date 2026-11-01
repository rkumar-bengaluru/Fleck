"""documents.progress

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'


def upgrade() -> None:
    op.add_column('documents', sa.Column('progress_done', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('documents', sa.Column('progress_total', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('documents', 'progress_total')
    op.drop_column('documents', 'progress_done')
