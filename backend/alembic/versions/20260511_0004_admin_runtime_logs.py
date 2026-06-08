"""admin_runtime_logs for persisted admin console logs

Revision ID: 20260511_0004
Revises: 20260511_0003
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260511_0004"
down_revision = "20260511_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_runtime_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("logger", sa.String(length=256), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_runtime_logs_created_at", "admin_runtime_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_admin_runtime_logs_created_at", table_name="admin_runtime_logs")
    op.drop_table("admin_runtime_logs")
