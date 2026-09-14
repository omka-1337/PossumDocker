"""add server ports and state

Revision ID: 5881748df1a9
Revises: df6342b595c2
Create Date: 2026-09-15 01:03:16.912799

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '5881748df1a9'
down_revision: str | None = 'df6342b595c2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ports', sa.JSON(), nullable=False, server_default='{}'))
        batch_op.add_column(sa.Column('state_message', sa.String(length=1000), nullable=True))
        batch_op.alter_column('status', new_column_name='state', existing_type=sa.String(length=32))

    # Old rows were never installed, whatever status they had.
    op.execute("UPDATE servers SET state = 'pending'")


def downgrade() -> None:
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.alter_column('state', new_column_name='status', existing_type=sa.String(length=32))
        batch_op.drop_column('state_message')
        batch_op.drop_column('ports')
