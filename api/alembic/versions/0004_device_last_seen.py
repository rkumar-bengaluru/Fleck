"""devices.last_seen

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'


def upgrade() -> None:
    op.add_column('devices', sa.Column('last_seen', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'last_seen')
