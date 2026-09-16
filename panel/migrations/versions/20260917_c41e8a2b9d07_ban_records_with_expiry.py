"""ban records with expiry

Revision ID: c41e8a2b9d07
Revises: 2310205f45df
Create Date: 2026-09-17 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'c41e8a2b9d07'
down_revision: str | None = '2310205f45df'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('panel_bans') as batch_op:
        batch_op.drop_index('ix_panel_bans_server_id')
    op.rename_table('panel_bans', 'ban_records')
    with op.batch_alter_table('ban_records') as batch_op:
        batch_op.add_column(sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('banned_by', sa.String(length=32), nullable=True))
        batch_op.create_index('ix_ban_records_server_id', ['server_id'], unique=False)
        batch_op.create_index('ix_ban_records_expires_at', ['expires_at'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('ban_records') as batch_op:
        batch_op.drop_index('ix_ban_records_expires_at')
        batch_op.drop_index('ix_ban_records_server_id')
        batch_op.drop_column('banned_by')
        batch_op.drop_column('expires_at')
    op.rename_table('ban_records', 'panel_bans')
    with op.batch_alter_table('panel_bans') as batch_op:
        batch_op.create_index('ix_panel_bans_server_id', ['server_id'], unique=False)
