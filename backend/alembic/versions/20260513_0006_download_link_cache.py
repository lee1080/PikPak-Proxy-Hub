"""add download_link_cache table

Revision ID: 20260513_0006
Revises: 20260512_0005
Create Date: 2026-05-13 16:25:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260513_0006"
down_revision = "20260512_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "download_link_cache",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("episode_key", sa.String(length=32), nullable=True),
        sa.Column("episode_season", sa.Integer(), nullable=True),
        sa.Column("episode_number", sa.Integer(), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("sort_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "file_id", name="uq_download_link_cache_task_file"),
    )
    op.create_index("idx_dlc_task_expires", "download_link_cache", ["task_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("idx_dlc_task_expires", table_name="download_link_cache")
    op.drop_table("download_link_cache")
