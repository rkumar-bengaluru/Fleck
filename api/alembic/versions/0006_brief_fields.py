"""briefs: context, doc_count, error, updated_at; llm_calls.event_id

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'


def upgrade() -> None:
    op.add_column('briefs', sa.Column('context', sa.Text(), nullable=True))
    op.add_column('briefs', sa.Column('doc_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('briefs', sa.Column('error', sa.Text(), nullable=True))
    op.add_column('briefs', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.create_unique_constraint('uq_briefs_event_id', 'briefs', ['event_id'])
    op.add_column('llm_calls', sa.Column('event_id', sa.Integer(), nullable=True))
    op.create_foreign_key('llm_calls_event_id_fkey', 'llm_calls', 'calendar_events', ['event_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('llm_calls_event_id_fkey', 'llm_calls', type_='foreignkey')
    op.drop_column('llm_calls', 'event_id')
    op.drop_constraint('uq_briefs_event_id', 'briefs', type_='unique')
    for c in ('updated_at', 'error', 'doc_count', 'context'):
        op.drop_column('briefs', c)
