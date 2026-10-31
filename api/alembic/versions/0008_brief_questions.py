"""briefs.questions

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '0008'
down_revision = '0007'


def upgrade() -> None:
    op.add_column('briefs', sa.Column('questions', JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('briefs', 'questions')
