"""calendar_events.recurring_event_id and cancelled

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'


def upgrade() -> None:
    op.add_column('calendar_events', sa.Column('recurring_event_id', sa.String(), nullable=True))
    op.add_column('calendar_events', sa.Column('cancelled', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column('calendar_events', 'cancelled')
    op.drop_column('calendar_events', 'recurring_event_id')
