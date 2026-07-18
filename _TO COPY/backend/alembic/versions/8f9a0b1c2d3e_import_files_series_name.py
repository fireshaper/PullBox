"""import_files by scanned series name (no series FK)

Revision ID: 8f9a0b1c2d3e
Revises: 7e8f9a0b1c2d
Create Date: 2026-07-11 00:00:00.000000

Recreates ``import_files`` so imports record the *scanned* series name/year (not a
pre-created Series). Matching to a ComicVine volume now happens in the background
scheduler, so the import endpoint makes no ComicVine calls. The table holds only
transient backlog rows, so a drop+recreate is safe.
"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '8f9a0b1c2d3e'
down_revision: Union[str, Sequence[str], None] = '7e8f9a0b1c2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index('ix_import_files_series_id', table_name='import_files')
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


def downgrade() -> None:
    op.drop_index('ix_import_files_series_title', table_name='import_files')
    op.drop_table('import_files')
    op.create_table(
        'import_files',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('series_id', sa.Integer(), nullable=False),
        sa.Column('issue_number', sa.String(), nullable=False),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['series_id'], ['series.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_import_files_series_id', 'import_files', ['series_id'])
