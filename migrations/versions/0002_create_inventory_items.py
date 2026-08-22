"""Create the administrator-managed inventory catalog.

Revision ID: 0002_inventory_items
Revises: 0001_web_sessions
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_inventory_items"
down_revision: str | Sequence[str] | None = "0001_web_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("stock_quantity", sa.Integer(), nullable=False),
        sa.Column("image_filename", sa.String(length=37), nullable=True),
        sa.Column("is_visible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "stock_quantity >= 0", name="ck_inventory_items_stock_nonnegative"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_items_is_visible", "inventory_items", ["is_visible"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_items_is_visible", table_name="inventory_items")
    op.drop_table("inventory_items")
