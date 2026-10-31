"""assists.late: the answer came from the helper's late recheck (decision 128)

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'


def upgrade() -> None:
    op.add_column('assists', sa.Column('late', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('assists', 'late')
