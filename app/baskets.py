from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Booking, BookingSelection, InventoryItem

MAX_SELECTED_QUANTITY = 2_147_483_647
MAX_SELECTED_QUANTITY_DIGITS = len(str(MAX_SELECTED_QUANTITY))


@dataclass(frozen=True)
class BasketItem:
    item: InventoryItem
    selected_quantity: int

    @property
    def remaining_quantity(self) -> int:
        return self.item.stock_quantity - self.selected_quantity

    @property
    def has_negative_remaining(self) -> bool:
        return self.remaining_quantity < 0


def parse_selected_quantity(raw_value: str) -> tuple[int | None, str | None]:
    value = raw_value.strip()
    if not value:
        return None, "Enter a selected quantity."
    if not value.isascii() or not value.isdigit():
        return None, "Selected quantity must be a nonnegative whole number."
    if len(value) > MAX_SELECTED_QUANTITY_DIGITS:
        return None, "Selected quantity is too large."
    quantity = int(value)
    if quantity > MAX_SELECTED_QUANTITY:
        return None, "Selected quantity is too large."
    return quantity, None


def customer_basket_items(
    session: Session,
    booking_id: int,
    *,
    query_text: str = "",
    basket_only: bool = False,
) -> list[BasketItem]:
    return _basket_items(
        session,
        booking_id,
        query_text=query_text,
        basket_only=basket_only,
        include_hidden_selected=basket_only,
    )


def admin_basket_items(session: Session, booking_id: int) -> list[BasketItem]:
    """Return every visible item plus hidden selections for one booking."""

    return _basket_items(
        session,
        booking_id,
        include_hidden_selected=True,
    )


def _basket_items(
    session: Session,
    booking_id: int,
    *,
    query_text: str = "",
    basket_only: bool = False,
    include_hidden_selected: bool = False,
) -> list[BasketItem]:
    selection_quantity = BookingSelection.selected_quantity
    statement = select(InventoryItem, selection_quantity).outerjoin(
        BookingSelection,
        (BookingSelection.inventory_item_id == InventoryItem.id)
        & (BookingSelection.booking_id == booking_id),
    )
    if basket_only:
        statement = statement.where(selection_quantity.is_not(None))
    elif include_hidden_selected:
        statement = statement.where(
            or_(
                InventoryItem.is_visible.is_(True),
                selection_quantity.is_not(None),
            )
        )
    else:
        statement = statement.where(InventoryItem.is_visible.is_(True))

    rows = session.execute(
        statement.order_by(InventoryItem.name.asc(), InventoryItem.id.asc())
    ).all()
    normalized_query = query_text.casefold()
    items: list[BasketItem] = []
    for item, selected_quantity in rows:
        quantity = selected_quantity or 0
        if basket_only and quantity == 0:
            continue
        if normalized_query and normalized_query not in (
            f"{item.name}\n{item.description or ''}".casefold()
        ):
            continue
        items.append(BasketItem(item=item, selected_quantity=quantity))
    return items


def basket_snapshot(session: Session, booking_id: int) -> dict[str, object]:
    revision = session.execute(
        select(Booking.revision).where(Booking.id == booking_id)
    ).scalar_one()
    items = _basket_items(session, booking_id, include_hidden_selected=True)
    selections = {
        str(row.item.id): {
            "quantity": row.selected_quantity,
            "stock_quantity": row.item.stock_quantity,
            "remaining_quantity": row.remaining_quantity,
            "is_available": row.item.is_visible,
        }
        for row in items
    }
    selected_items = [row for row in items if row.selected_quantity > 0]
    return {
        "revision": revision,
        "selections": selections,
        "totals": {
            "item_types": len(selected_items),
            "units": sum(row.selected_quantity for row in selected_items),
        },
    }


def replace_selection(
    session: Session,
    *,
    booking_id: int,
    inventory_item_id: int,
    quantity: int,
    now: datetime,
) -> None:
    selection = session.get(BookingSelection, (booking_id, inventory_item_id))
    if quantity == 0:
        if selection is not None:
            session.delete(selection)
        return
    if selection is None:
        session.add(
            BookingSelection(
                booking_id=booking_id,
                inventory_item_id=inventory_item_id,
                selected_quantity=quantity,
                created_at=now,
                updated_at=now,
            )
        )
        return
    selection.selected_quantity = quantity
    selection.updated_at = now
