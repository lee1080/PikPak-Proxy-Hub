"""init tables

Revision ID: 20260509_0001
Revises:
Create Date: 2026-05-09 14:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260509_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=True),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="FREE"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "pikpak_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=128), nullable=False),
        sa.Column("password_enc", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("device_id", sa.String(length=64), nullable=False),
        sa.Column("pool_type", sa.String(length=16), nullable=False, server_default="FREE"),
        sa.Column("quota_total", sa.BigInteger(), nullable=False, server_default="6442450944"),
        sa.Column("quota_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("daily_tasks_left", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ACTIVE"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("pikpak_account_id", sa.Uuid(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("link_type", sa.String(length=16), nullable=False),
        sa.Column("pikpak_task_id", sa.String(length=64), nullable=True),
        sa.Column("file_id", sa.String(length=64), nullable=True),
        sa.Column("file_name", sa.String(length=512), nullable=True),
        sa.Column("file_size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["pikpak_account_id"], ["pikpak_accounts.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("idx_tasks_user", "tasks", ["user_id"])
    op.create_index("idx_tasks_status", "tasks", ["status"])
    op.create_index("idx_tasks_account", "tasks", ["pikpak_account_id"])
    op.create_index("idx_accounts_pool", "pikpak_accounts", ["pool_type", "status"])


def downgrade() -> None:
    op.drop_index("idx_accounts_pool", table_name="pikpak_accounts")
    op.drop_index("idx_tasks_account", table_name="tasks")
    op.drop_index("idx_tasks_status", table_name="tasks")
    op.drop_index("idx_tasks_user", table_name="tasks")
    op.drop_table("tasks")
    op.drop_table("pikpak_accounts")
    op.drop_table("users")
