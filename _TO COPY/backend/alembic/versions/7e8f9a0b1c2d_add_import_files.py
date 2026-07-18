"""add_import_files

Revision ID: 7e8f9a0b1c2d
Revises: 6d7e8f9a0b1c
Create Date: 2026-07-11 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '7e8f9a0b1c2d'
down_revision: Union[str, Sequence[str], None] = '6d7e8f9a0b1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the import_files table (pending scanned files awaiting issue-sync)."""
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


def downgrade() -> None:
    """Drop the import_files table."""
    op.drop_index('ix_import_files_series_id', table_name='import_files')
    op.drop_table('import_files')
