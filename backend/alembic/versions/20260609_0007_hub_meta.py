"""hub_meta for persisted scheduler state

Revision ID: 20260609_0007
Revises: 20260513_0006
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260609_0007"
down_revision = "20260513_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hub_meta",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=256), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("hub_meta")
