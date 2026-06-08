"""pikpak_accounts.hub_folder_id PPHUB 目录

Revision ID: 20260512_0005
Revises: 20260511_0004
Create Date: 2026-05-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260512_0005"
down_revision = "20260511_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pikpak_accounts",
        sa.Column("hub_folder_id", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pikpak_accounts", "hub_folder_id")
