"""Create anonymous bookings and link booking sessions.

Revision ID: 0003_bookings
Revises: 0002_inventory_items
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_bookings"
down_revision: str | Sequence[str] | None = "0002_inventory_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_reference", sa.String(length=32), nullable=False),
        sa.Column("access_code_digest", sa.String(length=64), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "revision >= 0", name="ck_bookings_revision_nonnegative"
        ),
        sa.CheckConstraint(
            "length(access_code_digest) = 64",
            name="ck_bookings_access_code_digest_length",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "public_reference", name="uq_bookings_public_reference"
        ),
        sa.UniqueConstraint(
            "access_code_digest", name="uq_bookings_access_code_digest"
        ),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_bookings_event_date", "bookings", ["event_date"], unique=False
    )
    op.execute(
        """
        CREATE TRIGGER prevent_booking_reference_update
        BEFORE UPDATE OF public_reference ON bookings
        WHEN OLD.public_reference <> NEW.public_reference
             AND OLD.public_reference <> ''
        BEGIN
            SELECT RAISE(ABORT, 'booking public reference is immutable');
        END
        """
    )

    with op.batch_alter_table("web_sessions", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("booking_id", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_web_sessions_booking_actor_consistency",
            "(actor_type = 'admin' AND booking_id IS NULL) OR "
            "(actor_type = 'booking' AND booking_id IS NOT NULL)",
        )
        batch_op.create_foreign_key(
            "fk_web_sessions_booking_id_bookings",
            "bookings",
            ["booking_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_web_sessions_booking_id", ["booking_id"])


def downgrade() -> None:
    op.execute("DROP TRIGGER prevent_booking_reference_update")
    with op.batch_alter_table("web_sessions", recreate="always") as batch_op:
        batch_op.drop_index("ix_web_sessions_booking_id")
        batch_op.drop_constraint(
            "fk_web_sessions_booking_id_bookings", type_="foreignkey"
        )
        batch_op.drop_constraint(
            "ck_web_sessions_booking_actor_consistency", type_="check"
        )
        batch_op.drop_column("booking_id")

    op.drop_index("ix_bookings_event_date", table_name="bookings")
    op.drop_table("bookings")
