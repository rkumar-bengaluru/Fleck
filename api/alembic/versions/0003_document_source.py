"""document content_hash and source_path

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'


def upgrade() -> None:
    op.add_column('documents', sa.Column('content_hash', sa.String(), nullable=True))
    op.add_column('documents', sa.Column('source_path', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('documents', 'source_path')
    op.drop_column('documents', 'content_hash')
