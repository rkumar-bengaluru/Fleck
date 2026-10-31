"""chat_threads: one per meeting, one for Ask-all

Revision ID: 0010
Revises: 0009
"""
from alembic import op


revision = '0010'
down_revision = '0009'


def upgrade() -> None:
    op.execute("DELETE FROM chat_threads a USING chat_threads b WHERE a.id > b.id AND a.meeting_id IS NOT DISTINCT FROM b.meeting_id")
    op.create_index('uq_chat_threads_meeting', 'chat_threads', ['meeting_id'], unique=True, postgresql_where='meeting_id IS NOT NULL')
    op.execute("CREATE UNIQUE INDEX uq_chat_threads_ask_all ON chat_threads ((true)) WHERE meeting_id IS NULL")


def downgrade() -> None:
    op.drop_index('uq_chat_threads_ask_all', 'chat_threads')
    op.drop_index('uq_chat_threads_meeting', 'chat_threads')
