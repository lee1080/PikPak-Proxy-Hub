"""add must_change_password to users

Revision ID: 20260509_0002
Revises: 20260509_0001
Create Date: 2026-05-09 14:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260509_0002"
down_revision = "20260509_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")

