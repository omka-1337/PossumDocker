"""add server disk and backup limits

Revision ID: b2d4f6a8c1e3
Revises: a7c3e91d2b40
Create Date: 2026-09-16 15:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'b2d4f6a8c1e3'
down_revision: str | None = 'a7c3e91d2b40'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('servers') as batch_op:
        batch_op.add_column(sa.Column('disk_limit_mb', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('backup_limit_mb', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servers') as batch_op:
        batch_op.drop_column('backup_limit_mb')
        batch_op.drop_column('disk_limit_mb')
