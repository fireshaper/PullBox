"""add file_issues

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-26

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e4f5a6b7c8d9'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a6b7c8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'file_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('issue_id', sa.Integer(), nullable=True),
        sa.Column('file_path', sa.String(), nullable=False),
        sa.Column('kind', sa.String(), nullable=False),
        sa.Column('severity', sa.String(), nullable=False, server_default='error'),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=True),
        sa.Column('detected_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['issue_id'], ['issues.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_file_issues_issue_id', 'file_issues', ['issue_id'])
    op.create_index('ix_file_issues_file_path', 'file_issues', ['file_path'])
    op.create_index('ix_file_issues_kind', 'file_issues', ['kind'])


def downgrade() -> None:
    op.drop_index('ix_file_issues_kind', table_name='file_issues')
    op.drop_index('ix_file_issues_file_path', table_name='file_issues')
    op.drop_index('ix_file_issues_issue_id', table_name='file_issues')
    op.drop_table('file_issues')
