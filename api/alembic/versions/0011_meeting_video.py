"""meeting video: video_chunks and meetings.video_key

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op


revision = '0011'
down_revision = '0010'


def upgrade() -> None:
    op.add_column('meetings', sa.Column('video_key', sa.String(), nullable=True))
    op.create_table(
        'video_chunks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('meeting_id', sa.Integer(), sa.ForeignKey('meetings.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start_ms', sa.Integer(), nullable=False),
        sa.Column('blob_key', sa.String(), nullable=False),
        sa.UniqueConstraint('meeting_id', 'start_ms'),
    )


def downgrade() -> None:
    op.drop_table('video_chunks')
    op.drop_column('meetings', 'video_key')
