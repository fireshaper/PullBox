"""add_story_arcs

Revision ID: 5c6d7e8f9a0b
Revises: 4b5c6d7e8f9a
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '5c6d7e8f9a0b'
down_revision: Union[str, Sequence[str], None] = '4b5c6d7e8f9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add story_arcs, the issue↔arc link table, and issues.arcs_synced_at."""
    op.create_table(
        'story_arcs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('comicvine_id', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('publisher', sa.String(), nullable=True),
        sa.Column('cover_url', sa.String(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('count_of_issue_appearances', sa.Integer(), nullable=True),
        sa.Column('detail_synced_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('comicvine_id'),
    )
    op.create_table(
        'issue_story_arcs',
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('story_arc_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id']),
        sa.ForeignKeyConstraint(['story_arc_id'], ['story_arcs.id']),
        sa.PrimaryKeyConstraint('issue_id', 'story_arc_id'),
    )
    op.add_column('issues', sa.Column('arcs_synced_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Drop story arc tables and the issues.arcs_synced_at column."""
    op.drop_column('issues', 'arcs_synced_at')
    op.drop_table('issue_story_arcs')
    op.drop_table('story_arcs')
