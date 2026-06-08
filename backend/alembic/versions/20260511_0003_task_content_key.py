"""tasks: content_key for shared offline cache

Revision ID: 20260511_0003
Revises: 20260509_0002
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260511_0003"
down_revision = "20260509_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("content_key", sa.String(length=256), nullable=True))
    op.create_index("ix_tasks_content_key_status", "tasks", ["content_key", "status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_content_key_status", table_name="tasks")
    op.drop_column("tasks", "content_key")
