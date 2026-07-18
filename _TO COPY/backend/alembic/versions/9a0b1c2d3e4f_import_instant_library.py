"""instant import library: nullable comicvine_id + import_files tracking

Revision ID: 9a0b1c2d3e4f
Revises: 8f9a0b1c2d3e
Create Date: 2026-07-15 00:00:00.000000

Library import now creates the real ``Series``/``Issue`` rows immediately (with
``comicvine_id = NULL``), so both columns become nullable. ``import_files`` is
rebuilt as a per-issue ComicVine-sync tracking table (FKs to the created
issue/series). It holds only transient tracking rows, so a drop+recreate is safe.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9a0b1c2d3e4f'
down_revision: Union[str, Sequence[str], None] = '8f9a0b1c2d3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('series') as batch_op:
        batch_op.alter_column(
            'comicvine_id', existing_type=sa.String(), nullable=True
        )
    with op.batch_alter_table('issues') as batch_op:
        batch_op.alter_column(
            'comicvine_id', existing_type=sa.String(), nullable=True
        )

    op.drop_index('ix_import_files_series_title', table_name='import_files')
    op.drop_table('import_files')
    op.create_table(
        'import_files',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=False),
        sa.Column('series_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('synced_at', sa.DateTime(), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id']),
        sa.ForeignKeyConstraint(['series_id'], ['series.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_import_files_issue_id', 'import_files', ['issue_id'])
    op.create_index('ix_import_files_series_id', 'import_files', ['series_id'])


def downgrade() -> None:
    op.drop_index('ix_import_files_series_id', table_name='import_files')
    op.drop_index('ix_import_files_issue_id', table_name='import_files')
    op.drop_table('import_files')
    op.create_table(
        'import_files',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('series_title', sa.String(), nullable=False),
        sa.Column('series_year', sa.Integer(), nullable=True),
        sa.Column('issue_number', sa.String(), nullable=False),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_import_files_series_title', 'import_files', ['series_title'])

    with op.batch_alter_table('issues') as batch_op:
        batch_op.alter_column(
            'comicvine_id', existing_type=sa.String(), nullable=False
        )
    with op.batch_alter_table('series') as batch_op:
        batch_op.alter_column(
            'comicvine_id', existing_type=sa.String(), nullable=False
        )
