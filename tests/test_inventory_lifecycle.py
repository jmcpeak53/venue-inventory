from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

import pytest
from flask.testing import FlaskClient
from sqlalchemy import event, insert, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.images import image_directory
from app.models import Booking, BookingSelection, InventoryItem
from app.times import naive_utc
from app.views import admin as admin_views
from tests.conftest import csrf_token, sign_in
from tests.test_admin_catalog import image_bytes, submit_item
from tests.test_baskets import (
    admin_save,
    customer_basket_page,
    customer_save,
    page_revision,
)
from tests.test_bookings import create_booking, customer_sign_in


def update_item_stock(
    client: FlaskClient,
    item_id: int,
    *,
    name: str,
    stock_quantity: int,
) -> None:
    form = client.get(f"/admin/items/{item_id}/edit")
    response = client.post(
        f"/admin/items/{item_id}",
        data={
            "csrf_token": csrf_token(form),
            "name": name,
            "description": "",
            "stock_quantity": str(stock_quantity),
            "is_visible": "1",
        },
    )
    assert response.status_code == 302


def toggle_item_visibility(client: FlaskClient, item_id: int) -> None:
    detail = client.get(f"/admin/items/{item_id}")
    response = client.post(
        f"/admin/items/{item_id}/visibility",
        data={"csrf_token": csrf_token(detail)},
    )
    assert response.status_code == 302


def article_containing(body: str, marker: str) -> str:
    marker_index = body.index(marker)
    start = body.rfind("<article", 0, marker_index)
    end = body.index("</article>", marker_index)
    return body[start:end]


def tag_with_attribute(body: str, attribute: str) -> str:
    match = re.search(rf"<[^>]+\b{re.escape(attribute)}\b[^>]*>", body)
    assert match is not None
    return match.group(0)


def test_stock_reduction_preserves_each_selection_and_warns_only_affected_booking(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="10").status_code
        == 302
    )
    first_code = create_booking(client, "2026-09-01")
    second_code = create_booking(client, "2026-09-02")
    assert admin_save(client, 1, 1, "8").status_code == 302
    assert admin_save(client, 2, 1, "2").status_code == 302

    update_item_stock(client, 1, name="Chair", stock_quantity=5)

    first_customer = app.test_client()
    second_customer = app.test_client()
    assert customer_sign_in(first_customer, first_code).status_code == 302
    assert customer_sign_in(second_customer, second_code).status_code == 302
    first_body = customer_basket_page(first_customer).get_data(as_text=True)
    second_body = customer_basket_page(second_customer).get_data(as_text=True)
    assert ">-3</dd>" in first_body
    assert " hidden" not in tag_with_attribute(first_body, "data-remaining-warning")
    assert ">3</dd>" in second_body
    assert " hidden" in tag_with_attribute(second_body, "data-remaining-warning")

    first_admin = client.get("/admin/bookings/1").get_data(as_text=True)
    second_admin = client.get("/admin/bookings/2").get_data(as_text=True)
    assert "Remaining for this booking: <strong class=\"negative-remaining-value\">-3" in first_admin
    assert "Negative remaining: this booking selects" in first_admin
    assert "Negative remaining: this booking selects" not in second_admin

    booking_list = client.get("/admin/bookings").get_data(as_text=True)
    assert "Negative remaining: one or more selections exceed current stock." in article_containing(
        booking_list, "B-0001"
    )
    assert "Negative remaining" not in article_containing(booking_list, "B-0002")

    with app.app_context():
        first_selection = get_session().get(BookingSelection, (1, 1))
        second_selection = get_session().get(BookingSelection, (2, 1))
        assert first_selection is not None
        assert second_selection is not None
        assert first_selection.selected_quantity == 8
        assert second_selection.selected_quantity == 2


def test_hidden_selection_is_basket_only_customer_locked_and_admin_editable(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="5").status_code
        == 302
    )
    selected_code = create_booking(client, "2026-09-01")
    unselected_code = create_booking(client, "2026-09-02")
    selected_customer = app.test_client()
    unselected_customer = app.test_client()
    assert customer_sign_in(selected_customer, selected_code).status_code == 302
    assert customer_sign_in(unselected_customer, unselected_code).status_code == 302
    assert customer_save(selected_customer, 1, "3", revision=0).status_code == 200
    stale_page = customer_basket_page(selected_customer)

    toggle_item_visibility(client, 1)

    assert "Chair" not in customer_basket_page(selected_customer).get_data(
        as_text=True
    )
    selected_basket = selected_customer.get("/customer/portal?view=basket")
    selected_body = selected_basket.get_data(as_text=True)
    selected_article = article_containing(selected_body, 'data-item-id="1"')
    assert "Chair" in selected_article
    assert "Unavailable" in selected_article
    assert 'data-is-available="false"' in selected_article
    assert "disabled" in selected_article
    assert 'value="3"' in selected_article
    assert 'id="basket-item-types">1<' in selected_body
    assert 'id="basket-units">3<' in selected_body

    assert "Chair" not in customer_basket_page(unselected_customer).get_data(
        as_text=True
    )
    assert "Chair" not in unselected_customer.get(
        "/customer/portal?view=basket"
    ).get_data(as_text=True)

    locked_save = selected_customer.post(
        "/customer/selections/1",
        data={
            "csrf_token": csrf_token(stale_page),
            "quantity": "2",
            "revision": page_revision(stale_page),
        },
        headers={"Accept": "application/json"},
    )
    assert locked_save.status_code == 409
    assert locked_save.get_json()["code"] == "item_unavailable"
    assert locked_save.get_json()["selections"]["1"]["quantity"] == 3
    assert locked_save.get_json()["selections"]["1"]["is_available"] is False

    hidden_admin = client.get("/admin/bookings/1").get_data(as_text=True)
    assert "Hidden from customers" in hidden_admin
    assert admin_save(client, 1, 1, "2").status_code == 302

    toggle_item_visibility(client, 1)
    shown_body = customer_basket_page(selected_customer).get_data(as_text=True)
    shown_article = article_containing(shown_body, 'data-item-id="1"')
    assert 'data-is-available="true"' in shown_article
    assert "disabled" not in shown_article
    assert customer_save(selected_customer, 1, "1").status_code == 200

    toggle_item_visibility(client, 1)
    assert admin_save(client, 1, 1, "0").status_code == 302
    with app.app_context():
        assert get_session().get(BookingSelection, (1, 1)) is None
        assert get_session().get(Booking, 1).event_date.isoformat() == "2026-09-01"


def test_stale_revision_after_hide_and_admin_edit_marks_selection_unavailable(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="5").status_code
        == 302
    )
    code = create_booking(client, "2026-09-01")
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302
    assert customer_save(customer, 1, "3", revision=0).status_code == 200
    stale_page = customer_basket_page(customer)

    toggle_item_visibility(client, 1)
    assert admin_save(client, 1, 1, "2").status_code == 302

    stale = customer.post(
        "/customer/selections/1",
        data={
            "csrf_token": csrf_token(stale_page),
            "quantity": "4",
            "revision": page_revision(stale_page),
        },
        headers={"Accept": "application/json"},
    )
    assert stale.status_code == 409
    payload = stale.get_json()
    assert payload["code"] == "stale_revision"
    assert payload["selections"]["1"]["quantity"] == 2
    assert payload["selections"]["1"]["is_available"] is False
    with app.app_context():
        selection = get_session().get(BookingSelection, (1, 1))
        assert selection is not None
        assert selection.selected_quantity == 2


def test_item_delete_lists_all_current_future_blockers_and_preserves_bookings(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="5").status_code
        == 302
    )
    assert (
        submit_item(client, "/admin/items", name="Lamp", quantity="5").status_code
        == 302
    )
    codes = [
        create_booking(client, "2026-08-21"),
        create_booking(client, "2026-08-22"),
        create_booking(client, "2026-08-23"),
        create_booking(client, "2026-08-23"),
    ]
    assert admin_save(client, 1, 1, "1").status_code == 302
    assert admin_save(client, 2, 1, "1").status_code == 302
    assert admin_save(client, 3, 1, "1").status_code == 302
    assert admin_save(client, 4, 2, "1").status_code == 302

    confirmation = client.get("/admin/items/1/delete")
    body = confirmation.get_data(as_text=True)
    assert "B-0002" in body and "2026-08-22" in body
    assert "B-0003" in body and "2026-08-23" in body
    assert "B-0001" not in body
    assert "B-0004" not in body
    assert all(code not in body for code in codes)
    assert "Hide the item instead" in body
    assert "Delete item permanently" not in body

    blocked = client.post(
        "/admin/items/1/delete",
        data={"csrf_token": csrf_token(confirmation)},
    )
    assert blocked.status_code == 409
    with app.app_context():
        assert get_session().get(InventoryItem, 1) is not None
        assert len(
            get_session()
            .execute(
                select(BookingSelection).where(
                    BookingSelection.inventory_item_id == 1
                )
            )
            .scalars()
            .all()
        ) == 3

    assert admin_save(client, 2, 1, "0").status_code == 302
    assert admin_save(client, 3, 1, "0").status_code == 302
    allowed = client.get("/admin/items/1/delete")
    deleted = client.post(
        "/admin/items/1/delete",
        data={"csrf_token": csrf_token(allowed)},
    )
    assert deleted.status_code == 302

    with app.app_context():
        assert get_session().get(InventoryItem, 1) is None
        assert get_session().get(InventoryItem, 2) is not None
        assert [
            booking.id
            for booking in get_session()
            .execute(select(Booking).order_by(Booking.id))
            .scalars()
        ] == [1, 2, 3, 4]
        selections = (
            get_session()
            .execute(select(BookingSelection).order_by(BookingSelection.booking_id))
            .scalars()
            .all()
        )
        assert [
            (selection.booking_id, selection.inventory_item_id)
            for selection in selections
        ] == [(4, 2)]


@pytest.mark.parametrize(
    ("instant", "event_date", "deletion_blocked"),
    [
        (datetime(2026, 3, 8, 5, 59, tzinfo=UTC), "2026-03-07", True),
        (datetime(2026, 3, 8, 6, 0, tzinfo=UTC), "2026-03-07", False),
        (datetime(2026, 3, 8, 7, 59, tzinfo=UTC), "2026-03-08", True),
        (datetime(2026, 3, 8, 8, 0, tzinfo=UTC), "2026-03-08", True),
        (datetime(2026, 11, 1, 6, 30, tzinfo=UTC), "2026-11-01", True),
        (datetime(2026, 11, 1, 7, 30, tzinfo=UTC), "2026-11-01", True),
    ],
)
def test_item_delete_uses_chicago_date_at_midnight_and_dst_transitions(
    app,
    client: FlaskClient,
    clock,
    instant: datetime,
    event_date: str,
    deletion_blocked: bool,
) -> None:
    clock.set(instant)
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Clock", quantity="1").status_code
        == 302
    )
    create_booking(client, event_date)
    assert admin_save(client, 1, 1, "1").status_code == 302

    confirmation = client.get("/admin/items/1/delete")
    body = confirmation.get_data(as_text=True)
    response = client.post(
        "/admin/items/1/delete",
        data={"csrf_token": csrf_token(confirmation)},
    )
    if deletion_blocked:
        assert "B-0001" in body
        assert response.status_code == 409
    else:
        assert "B-0001" not in body
        assert response.status_code == 302


def _statement_targets_inventory_items(execute_state) -> bool:
    if not execute_state.is_delete:
        return False
    table = getattr(execute_state.statement, "table", None)
    return getattr(table, "name", None) == InventoryItem.__tablename__


def test_past_selection_delete_rolls_back_rows_when_item_commit_fails(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="1").status_code
        == 302
    )
    create_booking(client, "2026-08-21")
    assert admin_save(client, 1, 1, "1").status_code == 302

    def fail_item_delete(execute_state) -> None:
        if _statement_targets_inventory_items(execute_state):
            raise SQLAlchemyError("injected item deletion failure")

    event.listen(Session, "do_orm_execute", fail_item_delete)
    try:
        confirmation = client.get("/admin/items/1/delete")
        response = client.post(
            "/admin/items/1/delete",
            data={"csrf_token": csrf_token(confirmation)},
        )
    finally:
        event.remove(Session, "do_orm_execute", fail_item_delete)

    assert response.status_code == 200
    assert "could not be deleted" in response.get_data(as_text=True)
    with app.app_context():
        assert get_session().get(InventoryItem, 1) is not None
        assert get_session().get(BookingSelection, (1, 1)) is not None
        assert get_session().get(Booking, 1) is not None


def test_item_delete_does_not_drop_a_current_selection_committed_after_the_guard_read(
    app, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="1").status_code
        == 302
    )
    create_booking(client, "2026-08-21")
    create_booking(client, "2026-08-23")
    assert admin_save(client, 1, 1, "1").status_code == 302

    original_blockers = admin_views._item_deletion_blockers
    injected = {"done": False}

    def blockers_then_commit_current_selection(
        item_id: int, today=None
    ) -> list[Booking]:
        found = original_blockers(item_id, today)
        if injected["done"] or found:
            return found
        injected["done"] = True
        other = app.extensions["session_factory"]()
        try:
            now = naive_utc(app.extensions["clock"].now())
            other.add(
                BookingSelection(
                    booking_id=2,
                    inventory_item_id=item_id,
                    selected_quantity=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            other.commit()
        finally:
            other.close()
        return found

    confirmation = client.get("/admin/items/1/delete")
    monkeypatch.setattr(
        admin_views, "_item_deletion_blockers", blockers_then_commit_current_selection
    )
    response = client.post(
        "/admin/items/1/delete",
        data={"csrf_token": csrf_token(confirmation)},
    )

    assert response.status_code == 409
    body = response.get_data(as_text=True)
    assert "B-0002" in body
    assert "2026-08-23" in body
    with app.app_context():
        assert get_session().get(InventoryItem, 1) is not None
        assert get_session().get(BookingSelection, (1, 1)) is not None
        committed = get_session().get(BookingSelection, (2, 1))
        assert committed is not None
        assert committed.selected_quantity == 1
        assert get_session().get(Booking, 1) is not None
        assert get_session().get(Booking, 2) is not None


def test_item_delete_does_not_drop_a_current_selection_added_during_the_transaction(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="1").status_code
        == 302
    )
    create_booking(client, "2026-08-21")
    create_booking(client, "2026-08-23")
    assert admin_save(client, 1, 1, "1").status_code == 302

    def inject_current_selection(execute_state) -> None:
        if not _statement_targets_inventory_items(execute_state):
            return
        now = naive_utc(app.extensions["clock"].now())
        execute_state.session.connection().execute(
            insert(BookingSelection.__table__),
            {
                "booking_id": 2,
                "inventory_item_id": 1,
                "selected_quantity": 1,
                "created_at": now,
                "updated_at": now,
            },
        )

    event.listen(Session, "do_orm_execute", inject_current_selection)
    try:
        confirmation = client.get("/admin/items/1/delete")
        response = client.post(
            "/admin/items/1/delete",
            data={"csrf_token": csrf_token(confirmation)},
        )
    finally:
        event.remove(Session, "do_orm_execute", inject_current_selection)

    assert response.status_code == 409
    body = response.get_data(as_text=True)
    assert "B-0002" in body
    assert "2026-08-23" in body
    assert "Hide the item instead" in body
    with app.app_context():
        assert get_session().get(InventoryItem, 1) is not None
        assert get_session().get(BookingSelection, (1, 1)) is not None
        assert get_session().get(Booking, 1) is not None
        assert get_session().get(Booking, 2) is not None


def test_image_cleanup_exception_after_delete_logs_recoverable_orphan(
    app,
    app_config,
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(
            client,
            "/admin/items",
            name="Lantern",
            quantity="1",
            image=(image_bytes("PNG"), "lantern.png", "image/png"),
        ).status_code
        == 302
    )
    create_booking(client, "2026-08-21")
    assert admin_save(client, 1, 1, "1").status_code == 302
    with app.app_context():
        image_filename = get_session().get(InventoryItem, 1).image_filename
    assert image_filename is not None
    orphan_path = image_directory(app_config.data_dir) / image_filename
    assert orphan_path.is_file()

    def fail_cleanup(_data_dir, _filename):
        raise OSError("injected cleanup failure")

    monkeypatch.setattr("app.views.admin.remove_normalized_image", fail_cleanup)
    confirmation = client.get("/admin/items/1/delete")
    with caplog.at_level(logging.WARNING, logger="venue_inventory.admin"):
        response = client.post(
            "/admin/items/1/delete",
            data={"csrf_token": csrf_token(confirmation)},
        )

    assert response.status_code == 302
    cleanup_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "inventory_image_cleanup_failed"
    ]
    assert len(cleanup_records) == 1
    assert cleanup_records[0].image_filename == image_filename
    assert orphan_path.is_file()
    with app.app_context():
        assert get_session().get(InventoryItem, 1) is None
        assert get_session().get(BookingSelection, (1, 1)) is None
        assert get_session().get(Booking, 1) is not None
