"""add webhooks

Outbound notification targets (Settings → Webhooks). One row per endpoint;
``events`` is a JSON list of the event names it subscribes to.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="generic"),
        sa.Column("events", sa.JSON(), nullable=False),
        sa.Column("secret", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_delivery_at", sa.DateTime(), nullable=True),
        sa.Column("last_delivery_success", sa.Boolean(), nullable=True),
        sa.Column("last_delivery_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("webhooks")
