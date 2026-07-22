"""add metron_id to series, issues, story_arcs

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-07-20 00:00:00.000000

Adds a nullable-unique ``metron_id`` to ``series``, ``issues`` and ``story_arcs`` so
Metron becomes the primary metadata identity while ``comicvine_id`` is retained as a
cross-reference. Also relaxes ``story_arcs.comicvine_id`` to nullable, since a
Metron-origin arc may have no ComicVine id. Columns are added nullable with a separate
unique index — the SQLite-friendly pattern (ALTER TABLE ADD COLUMN cannot carry an
inline UNIQUE constraint).
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a0b1c2d3e4f5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('series', sa.Column('metron_id', sa.String(), nullable=True))
    op.create_index('ix_series_metron_id', 'series', ['metron_id'], unique=True)

    op.add_column('issues', sa.Column('metron_id', sa.String(), nullable=True))
    op.create_index('ix_issues_metron_id', 'issues', ['metron_id'], unique=True)

    op.add_column('story_arcs', sa.Column('metron_id', sa.String(), nullable=True))
    op.create_index('ix_story_arcs_metron_id', 'story_arcs', ['metron_id'], unique=True)

    # ComicVine id is no longer mandatory on an arc (Metron-origin arcs may lack it).
    with op.batch_alter_table('story_arcs') as batch_op:
        batch_op.alter_column('comicvine_id', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('story_arcs') as batch_op:
        batch_op.alter_column('comicvine_id', existing_type=sa.String(), nullable=False)

    op.drop_index('ix_story_arcs_metron_id', table_name='story_arcs')
    op.drop_column('story_arcs', 'metron_id')

    op.drop_index('ix_issues_metron_id', table_name='issues')
    op.drop_column('issues', 'metron_id')

    op.drop_index('ix_series_metron_id', table_name='series')
    op.drop_column('series', 'metron_id')
