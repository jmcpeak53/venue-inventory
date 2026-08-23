"""Create independently evaluated booking selections.

Revision ID: 0004_booking_selections
Revises: 0003_bookings
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_booking_selections"
down_revision: str | Sequence[str] | None = "0003_bookings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "booking_selections",
        sa.Column("booking_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("selected_quantity", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "selected_quantity > 0",
            name="ck_booking_selections_quantity_positive",
        ),
        sa.ForeignKeyConstraint(
            ["booking_id"], ["bookings.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("booking_id", "inventory_item_id"),
    )
    op.create_index(
        "ix_booking_selections_inventory_item_id",
        "booking_selections",
        ["inventory_item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_booking_selections_inventory_item_id",
        table_name="booking_selections",
    )
    op.drop_table("booking_selections")
