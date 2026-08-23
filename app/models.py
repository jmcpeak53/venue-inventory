from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WebSession(Base):
    __tablename__ = "web_sessions"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('admin', 'booking')",
            name="ck_web_sessions_actor_type",
        ),
        CheckConstraint(
            "(actor_type = 'admin' AND booking_id IS NULL) OR "
            "(actor_type = 'booking' AND booking_id IS NOT NULL)",
            name="ck_web_sessions_booking_actor_consistency",
        ),
        UniqueConstraint("session_digest", name="uq_web_sessions_session_digest"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    booking_id: Mapped[int | None] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_bookings_revision_nonnegative"),
        CheckConstraint(
            "length(access_code_digest) = 64",
            name="ck_bookings_access_code_digest_length",
        ),
        UniqueConstraint("public_reference", name="uq_bookings_public_reference"),
        UniqueConstraint("access_code_digest", name="uq_bookings_access_code_digest"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_reference: Mapped[str] = mapped_column(String(32), nullable=False)
    access_code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class InventoryItem(Base):
    __tablename__ = "inventory_items"
    __table_args__ = (
        CheckConstraint(
            "stock_quantity >= 0", name="ck_inventory_items_stock_nonnegative"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stock_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    image_filename: Mapped[str | None] = mapped_column(String(37), nullable=True)
    is_visible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class BookingSelection(Base):
    __tablename__ = "booking_selections"
    __table_args__ = (
        CheckConstraint(
            "selected_quantity > 0",
            name="ck_booking_selections_quantity_positive",
        ),
    )

    booking_id: Mapped[int] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), primary_key=True
    )
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    selected_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
