"""add series.norm_title

Adds the normalized-title column used to recognise the same series across
Metron and ComicVine, whose ids live in separate namespaces.

The backfill runs in Python rather than as a SQL expression. A nested-REPLACE
formulation can only strip a character list you enumerate, and real titles
contain letters outside ASCII ("Kaijū No. 8") that the application's normalizer
drops but such a list would keep. That would leave a handful of rows with a key
that never matches what the app computes — silently defeating the column's whole
purpose for exactly the titles hardest to notice.

The normalizer is duplicated here on purpose: a migration must keep producing
the same result years later, so it must not import application code that may
change.

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
"""

import re
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def upgrade() -> None:
    op.add_column("series", sa.Column("norm_title", sa.String(), nullable=True))
    op.create_index("ix_series_norm_title", "series", ["norm_title"])

    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, title FROM series")).fetchall()
    for series_id, title in rows:
        conn.execute(
            sa.text("UPDATE series SET norm_title = :n WHERE id = :i"),
            {"n": _NON_ALNUM.sub("", (title or "").lower()), "i": series_id},
        )


def downgrade() -> None:
    op.drop_index("ix_series_norm_title", table_name="series")
    op.drop_column("series", "norm_title")
