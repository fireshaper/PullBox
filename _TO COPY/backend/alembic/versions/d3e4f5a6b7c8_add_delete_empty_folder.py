"""add_delete_empty_folder

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-07-25 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd3e4f5a6b7c8'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add post_processing_settings.delete_empty_folder (move-only cleanup flag)."""
    # server_default keeps existing rows valid under the NOT NULL constraint;
    # new rows get their value from the ORM default.
    op.add_column(
        'post_processing_settings',
        sa.Column(
            'delete_empty_folder',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop the delete_empty_folder column."""
    op.drop_column('post_processing_settings', 'delete_empty_folder')
