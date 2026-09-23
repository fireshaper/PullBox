"""add series.cv_lookup_at

Metron leaves ``cv_id`` null on most series, so a ComicVine id can only be
recovered by searching ComicVine for the title. That search costs an API call
and does not always find a confident match, so the attempt has to be recorded
somewhere — otherwise every sweep re-searches the same unmatchable series
forever and burns the hourly budget.

NULL means "never attempted", which is what every existing row gets: the
backfill sweep picks those up first.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("series", sa.Column("cv_lookup_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("series", "cv_lookup_at")
