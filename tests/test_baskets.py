from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
from datetime import timedelta
from pathlib import Path

import pytest
from flask import jsonify, request
from flask.testing import FlaskClient
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from werkzeug.serving import make_server

from app.db import get_session
from app.models import Booking, BookingSelection
from tests.conftest import csrf_token, sign_in
from tests.test_admin_catalog import submit_item
from tests.test_bookings import create_booking, customer_sign_in

REVISION_RE = re.compile(r'data-revision="(\d+)"')


def customer_basket_page(client: FlaskClient):
    response = client.get("/customer/portal")
    assert response.status_code == 200
    return response


def page_revision(response) -> int:
    match = REVISION_RE.search(response.get_data(as_text=True))
    assert match is not None
    return int(match.group(1))


def customer_save(
    client: FlaskClient,
    item_id: int,
    quantity: str,
    *,
    revision: int | None = None,
):
    page = customer_basket_page(client)
    return client.post(
        f"/customer/selections/{item_id}",
        data={
            "csrf_token": csrf_token(page),
            "quantity": quantity,
            "revision": page_revision(page) if revision is None else revision,
        },
        headers={"Accept": "application/json"},
    )


def admin_save(
    client: FlaskClient,
    booking_id: int,
    item_id: int,
    quantity: str,
):
    page = client.get(f"/admin/bookings/{booking_id}")
    assert page.status_code == 200
    return client.post(
        f"/admin/bookings/{booking_id}/selections/{item_id}",
        data={"csrf_token": csrf_token(page), "quantity": quantity},
    )


def test_customer_catalog_search_toggle_controls_and_accessible_autosave(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client,
        "/admin/items",
        name="Velvet chair",
        quantity="4",
        description="Deep blue seating",
    ).status_code == 302
    assert submit_item(
        client,
        "/admin/items",
        name="Hidden vase",
        quantity="9",
        visible=False,
    ).status_code == 302
    code = create_booking(client)

    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302
    page = customer_basket_page(customer)
    body = page.get_data(as_text=True)
    assert page.headers["Cache-Control"] == "no-store"
    assert "Velvet chair" in body
    assert "Deep blue seating" in body
    assert "Hidden vase" not in body
    assert "4" in body
    assert 'aria-label="No image available for Velvet chair"' in body
    assert 'name="q" type="search"' in body
    assert "All items" in body
    assert "My basket" in body
    assert 'id="basket-item-types">0<' in body
    assert 'id="basket-units">0<' in body
    assert 'max="4"' in body
    assert "Selected quantity" in body
    assert 'aria-live="polite">Saved' in body
    assert "data-retry" in body
    assert 'static/js/basket.js' in body

    by_description = customer.get("/customer/portal?q=BLUE")
    assert "Velvet chair" in by_description.get_data(as_text=True)
    no_match = customer.get("/customer/portal?q=table")
    assert "Velvet chair" not in no_match.get_data(as_text=True)
    assert "No catalog items match this search." in no_match.get_data(as_text=True)
    basket_only = customer.get("/customer/portal?view=basket")
    assert "Velvet chair" not in basket_only.get_data(as_text=True)
    assert "Your basket has no items" in basket_only.get_data(as_text=True)


def test_two_bookings_can_each_select_full_stock_and_zero_removes_only_one(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Round table", quantity="3"
    ).status_code == 302
    first_code = create_booking(client, "2026-09-01")
    second_code = create_booking(client, "2026-09-02")
    first = app.test_client()
    second = app.test_client()
    assert customer_sign_in(first, first_code).status_code == 302
    assert customer_sign_in(second, second_code).status_code == 302

    first_saved = customer_save(first, 1, "3", revision=0)
    second_saved = customer_save(second, 1, "3", revision=0)
    assert first_saved.status_code == 200
    assert second_saved.status_code == 200
    assert first_saved.get_json()["totals"] == {"item_types": 1, "units": 3}
    assert second_saved.get_json()["totals"] == {"item_types": 1, "units": 3}

    too_many = customer_save(first, 1, "4", revision=1)
    assert too_many.status_code == 422
    assert too_many.get_json()["code"] == "quantity_out_of_range"
    assert too_many.get_json()["revision"] == 1

    removed = customer_save(first, 1, "0", revision=1)
    assert removed.status_code == 200
    assert removed.get_json()["revision"] == 2
    assert removed.get_json()["totals"] == {"item_types": 0, "units": 0}

    with app.app_context():
        rows = get_session().execute(
            select(BookingSelection).order_by(BookingSelection.booking_id)
        ).scalars().all()
        assert [(row.booking_id, row.selected_quantity) for row in rows] == [(2, 3)]
        bookings = get_session().execute(
            select(Booking).order_by(Booking.id)
        ).scalars().all()
        assert [booking.revision for booking in bookings] == [2, 1]


def test_admin_overstock_edit_makes_older_customer_write_stale_and_retryable(
    app, client: FlaskClient, clock
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="3"
    ).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302
    stale_page = customer_basket_page(customer)
    stale_revision = page_revision(stale_page)
    stale_csrf = csrf_token(stale_page)

    clock.advance(timedelta(minutes=1))
    admin_response = admin_save(client, 1, 1, "7")
    assert admin_response.status_code == 302
    assert admin_response.headers["Location"].endswith(
        "/admin/bookings/1#selection-1"
    )

    stale = customer.post(
        "/customer/selections/1",
        data={
            "csrf_token": stale_csrf,
            "quantity": "2",
            "revision": stale_revision,
        },
        headers={"Accept": "application/json"},
    )
    assert stale.status_code == 409
    stale_payload = stale.get_json()
    assert stale_payload["code"] == "stale_revision"
    assert stale_payload["revision"] == 1
    assert stale_payload["selections"]["1"]["quantity"] == 7
    assert "retry" in stale_payload["message"].lower()

    retried = customer_save(customer, 1, "2", revision=stale_payload["revision"])
    assert retried.status_code == 200
    assert retried.get_json()["revision"] == 2
    with app.app_context():
        selection = get_session().get(BookingSelection, (1, 1))
        assert selection is not None
        assert selection.selected_quantity == 2
        assert get_session().get(Booking, 1).revision == 2


def test_saved_basket_totals_timestamps_and_later_session_persist(
    app, client: FlaskClient, clock
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="8"
    ).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Lamp", quantity="8"
    ).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302

    clock.advance(timedelta(seconds=5))
    first = customer_save(customer, 1, "2", revision=0)
    assert first.status_code == 200
    first_updated_at = clock.now().replace(tzinfo=None)
    clock.advance(timedelta(seconds=5))
    second = customer_save(customer, 2, "3", revision=1)
    assert second.status_code == 200
    assert second.get_json()["totals"] == {"item_types": 2, "units": 5}

    with app.app_context():
        booking = get_session().get(Booking, 1)
        assert booking is not None
        assert booking.updated_at == clock.now().replace(tzinfo=None)
        chair = get_session().get(BookingSelection, (1, 1))
        assert chair is not None
        assert chair.created_at == first_updated_at
        assert chair.updated_at == first_updated_at

    later = app.test_client()
    assert customer_sign_in(later, code).status_code == 302
    later_body = customer_basket_page(later).get_data(as_text=True)
    assert 'id="basket-item-types">2<' in later_body
    assert 'id="basket-units">5<' in later_body
    assert 'id="quantity-1"' in later_body
    assert 'value="2"' in later_body
    basket_body = later.get("/customer/portal?view=basket").get_data(as_text=True)
    assert "Chair" in basket_body
    assert "Lamp" in basket_body

    list_body = client.get("/admin/bookings").get_data(as_text=True)
    detail_body = client.get("/admin/bookings/1").get_data(as_text=True)
    assert "Last updated 2026-08-22 15:00:10 UTC" in list_body
    assert "Last updated" in detail_body
    assert "2026-08-22 15:00:10 UTC" in detail_body


def test_admin_zero_removes_selection_and_invalid_value_does_not_touch_revision(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="1"
    ).status_code == 302
    code = create_booking(client)
    del code

    above_stock = admin_save(client, 1, 1, "9")
    assert above_stock.status_code == 302
    detail = client.get("/admin/bookings/1").get_data(as_text=True)
    assert 'value="9"' in detail
    assert "Administrator quantities may exceed stock" in detail

    invalid = admin_save(client, 1, 1, "-1")
    assert invalid.status_code == 200
    invalid_body = invalid.get_data(as_text=True)
    assert 'aria-invalid="true"' in invalid_body
    assert "nonnegative whole number" in invalid_body
    with app.app_context():
        assert get_session().get(Booking, 1).revision == 1
        assert get_session().get(BookingSelection, (1, 1)).selected_quantity == 9

    removed = admin_save(client, 1, 1, "0")
    assert removed.status_code == 302
    with app.app_context():
        assert get_session().get(BookingSelection, (1, 1)) is None
        assert get_session().get(Booking, 1).revision == 2


@pytest.mark.parametrize("remove_via", ["hide", "delete"])
def test_customer_save_after_item_removed_returns_not_found_without_bumping_revision(
    app, client: FlaskClient, remove_via: str
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="3"
    ).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302
    open_page = customer_basket_page(customer)
    open_revision = page_revision(open_page)
    open_csrf = csrf_token(open_page)
    assert "Chair" in open_page.get_data(as_text=True)

    if remove_via == "hide":
        detail = client.get("/admin/items/1")
        removed = client.post(
            "/admin/items/1/visibility",
            data={"csrf_token": csrf_token(detail)},
        )
    else:
        confirmation = client.get("/admin/items/1/delete")
        removed = client.post(
            "/admin/items/1/delete",
            data={"csrf_token": csrf_token(confirmation)},
        )
    assert removed.status_code == 302

    response = customer.post(
        "/customer/selections/1",
        data={
            "csrf_token": open_csrf,
            "quantity": "2",
            "revision": open_revision,
        },
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 404
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["code"] == "item_not_found"
    assert payload["message"] == "This catalog item is no longer available."
    assert payload["revision"] == open_revision
    assert "1" not in payload["selections"]
    with app.app_context():
        assert get_session().get(BookingSelection, (1, 1)) is None
        assert get_session().get(Booking, 1).revision == open_revision


def test_transient_customer_failure_rolls_back_and_returns_actionable_retry(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="3"
    ).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302

    def fail_selection_commit(session: Session) -> None:
        if any(isinstance(row, BookingSelection) for row in session.new):
            raise SQLAlchemyError("injected transient basket failure")

    event.listen(Session, "before_commit", fail_selection_commit)
    try:
        failed = customer_save(customer, 1, "2", revision=0)
    finally:
        event.remove(Session, "before_commit", fail_selection_commit)

    assert failed.status_code == 503
    payload = failed.get_json()
    assert payload["code"] == "save_failed"
    assert "Retry" in payload["message"]
    assert payload["revision"] == 0
    with app.app_context():
        assert get_session().get(BookingSelection, (1, 1)) is None
        assert get_session().get(Booking, 1).revision == 0

    retry = customer_save(customer, 1, "2", revision=0)
    assert retry.status_code == 200


def test_basket_authorization_and_csrf_boundaries(app, client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="3"
    ).status_code == 302
    code = create_booking(client)

    anonymous = app.test_client()
    assert anonymous.get("/customer/portal").status_code == 302
    assert anonymous.post(
        "/customer/selections/1", data={"quantity": "1", "revision": "0"}
    ).status_code == 403

    admin_page = client.get("/admin/bookings/1")
    admin_to_customer = client.post(
        "/customer/selections/1",
        data={
            "csrf_token": csrf_token(admin_page),
            "quantity": "1",
            "revision": "0",
        },
    )
    assert admin_to_customer.status_code == 302
    assert admin_to_customer.headers["Location"].endswith("/customer/login")

    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302
    customer_page = customer_basket_page(customer)
    assert customer.post(
        "/customer/selections/1", data={"quantity": "1", "revision": "0"}
    ).status_code == 403
    customer_to_admin = customer.post(
        "/admin/bookings/1/selections/1",
        data={"csrf_token": csrf_token(customer_page), "quantity": "1"},
    )
    assert customer_to_admin.status_code == 302
    assert customer_to_admin.headers["Location"].endswith("/admin/login")

    with app.app_context():
        assert get_session().execute(select(BookingSelection)).scalars().all() == []


@pytest.mark.browser
def test_real_browser_rapid_changes_retry_persistence_and_responsive_controls(
    app, client: FlaskClient
) -> None:
    node = shutil.which("node")
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if node is None or chrome is None:
        pytest.skip("Node and Chrome are required for the real-browser smoke test.")

    fail_next_basket_write = {"remaining": 1}

    @app.before_request
    def inject_one_transient_basket_failure():
        if (
            fail_next_basket_write["remaining"]
            and request.method == "POST"
            and request.path.startswith("/customer/selections/")
        ):
            fail_next_basket_write["remaining"] = 0
            response = jsonify(
                {
                    "ok": False,
                    "code": "save_failed",
                    "message": "The change could not be saved. Retry.",
                }
            )
            response.status_code = 503
            return response
        return None

    assert sign_in(client).status_code == 302
    assert submit_item(
        client, "/admin/items", name="Chair", quantity="5"
    ).status_code == 302
    code = create_booking(client)

    try:
        server = make_server("127.0.0.1", 0, app, threaded=True)
    except PermissionError:
        pytest.skip("The sandbox does not permit the browser smoke HTTP socket.")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = Path(__file__).with_name("browser_smoke.mjs")
    try:
        completed = subprocess.run(
            [
                node,
                str(script),
                f"http://127.0.0.1:{server.server_port}",
                code,
                "1",
            ],
            env={"CHROME_EXECUTABLE": chrome},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["outcome"] == "passed"
    assert result["rapid_quantity"] == 3
    assert result["retry_visible"] is True
    assert result["mobile"]["noOverflow"] is True
    assert result["desktop"]["noOverflow"] is True

    with app.app_context():
        selection = get_session().get(BookingSelection, (1, 1))
        assert selection is not None
        assert selection.selected_quantity == 3
        assert get_session().get(Booking, 1).revision == 3
