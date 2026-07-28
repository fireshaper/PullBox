"""add_post_processing_settings

Revision ID: 6d7e8f9a0b1c
Revises: 5c6d7e8f9a0b
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '6d7e8f9a0b1c'
down_revision: Union[str, Sequence[str], None] = '5c6d7e8f9a0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the post_processing_settings singleton table."""
    op.create_table(
        'post_processing_settings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('operation', sa.String(), nullable=False),
        sa.Column('destination_root', sa.String(), nullable=True),
        sa.Column('folder_pattern', sa.String(), nullable=False),
        sa.Column('file_pattern', sa.String(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    """Drop the post_processing_settings table."""
    op.drop_table('post_processing_settings')
