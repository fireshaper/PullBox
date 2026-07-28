"""add_story_arc_subscription

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f5a6b7c8d9e0'
down_revision: Union[str, Sequence[str], None] = 'e4f5a6b7c8d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add story_arcs.subscribed / auto_download.

    server_default keeps existing arc rows valid under NOT NULL; new rows take
    their value from the ORM default.
    """
    op.add_column(
        'story_arcs',
        sa.Column('subscribed', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'story_arcs',
        sa.Column('auto_download', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Drop the subscription columns."""
    op.drop_column('story_arcs', 'auto_download')
    op.drop_column('story_arcs', 'subscribed')
