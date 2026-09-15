"""add server should_run and resource limits

Revision ID: a7c3e91d2b40
Revises: e13c8d6154c4
Create Date: 2026-09-15 18:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'a7c3e91d2b40'
down_revision: str | None = 'e13c8d6154c4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('servers') as batch_op:
        batch_op.add_column(sa.Column('should_run', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('memory_limit_mb', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('cpu_limit', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('servers') as batch_op:
        batch_op.drop_column('cpu_limit')
        batch_op.drop_column('memory_limit_mb')
        batch_op.drop_column('should_run')
