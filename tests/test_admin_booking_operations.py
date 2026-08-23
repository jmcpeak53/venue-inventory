from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import threading
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select
from werkzeug.serving import make_server

from app.access_codes import normalize_access_code
from app.db import get_session
from app.models import Booking, BookingSelection, WebSession
from tests.conftest import TEST_PASSWORD, csrf_token, sign_in
from tests.test_admin_catalog import submit_item
from tests.test_baskets import admin_save
from tests.test_bookings import create_booking, customer_sign_in
from tests.test_inventory_lifecycle import (
    article_containing,
    toggle_item_visibility,
    update_item_stock,
)

CODE_RE = re.compile(r"\b([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})\b")


def booking_list(
    client: FlaskClient, *, when: str | None = None, q: str | None = None
):
    params: list[str] = []
    if when is not None:
        params.append(f"when={when}")
    if q is not None:
        params.append(f"q={q}")
    query = f"?{'&'.join(params)}" if params else ""
    response = client.get(f"/admin/bookings{query}")
    assert response.status_code == 200
    return response


def lookup_booking(client: FlaskClient, code: str):
    page = booking_list(client)
    return client.post(
        "/admin/bookings/lookup",
        data={"csrf_token": csrf_token(page), "access_code": code},
    )


def update_event_date(client: FlaskClient, booking_id: int, event_date: str):
    page = client.get(f"/admin/bookings/{booking_id}")
    assert page.status_code == 200
    return client.post(
        f"/admin/bookings/{booking_id}",
        data={"csrf_token": csrf_token(page), "event_date": event_date},
    )


def test_booking_list_defaults_filters_and_orders_by_chicago_event_date(
    client: FlaskClient, clock
) -> None:
    assert sign_in(client).status_code == 302
    chicago_today = clock.now().astimezone(ZoneInfo("America/Chicago")).date()
    past = (chicago_today - timedelta(days=1)).isoformat()
    today = chicago_today.isoformat()
    future = (chicago_today + timedelta(days=2)).isoformat()
    soon = (chicago_today + timedelta(days=1)).isoformat()

    create_booking(client, future)
    create_booking(client, past)
    create_booking(client, today)
    create_booking(client, soon)

    default_body = booking_list(client).get_data(as_text=True)
    assert 'name="when"' in default_body
    assert re.search(
        r'<option value="upcoming"\s+selected>', default_body
    ) is not None
    assert "B-0001" in default_body
    assert "B-0003" in default_body
    assert "B-0004" in default_body
    assert "B-0002" not in default_body
    upcoming_order = [
        match.group(1)
        for match in re.finditer(r'href="/admin/bookings/(\d+)"', default_body)
    ]
    assert upcoming_order == ["3", "4", "1"]

    past_body = booking_list(client, when="past").get_data(as_text=True)
    past_refs = re.findall(r">B-(\d{4})</a>", past_body)
    assert past_refs == ["0002"]
    assert past in past_body

    all_body = booking_list(client, when="all").get_data(as_text=True)
    all_order = [
        match.group(1)
        for match in re.finditer(r'href="/admin/bookings/(\d+)"', all_body)
    ]
    assert all_order == ["2", "3", "4", "1"]


def test_booking_list_reference_search_and_per_booking_counts_match_detail(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="5").status_code
        == 302
    )
    assert (
        submit_item(client, "/admin/items", name="Lamp", quantity="3").status_code
        == 302
    )
    create_booking(client, "2026-09-01")
    create_booking(client, "2026-09-02")
    assert admin_save(client, 1, 1, "2").status_code == 302
    assert admin_save(client, 1, 2, "4").status_code == 302
    assert admin_save(client, 2, 1, "1").status_code == 302

    update_item_stock(client, 1, name="Chair", stock_quantity=1)
    toggle_item_visibility(client, 2)

    list_body = booking_list(client, when="all").get_data(as_text=True)
    first_row = article_containing(list_body, ">B-0001</a>")
    second_row = article_containing(list_body, ">B-0002</a>")
    assert "2 item types · 6 units" in first_row
    assert "Negative remaining: one or more selections exceed current stock." in first_row
    assert "Hidden item: one or more selections are hidden from customers." in first_row
    assert "1 item type · 1 unit" in second_row
    assert "Negative remaining" not in second_row
    assert "Hidden item" not in second_row

    detail = client.get("/admin/bookings/1").get_data(as_text=True)
    assert "Selected quantity: <strong data-preparation-quantity>2</strong>" in detail
    assert "Selected quantity: <strong data-preparation-quantity>4</strong>" in detail
    assert 'data-preparation-warning="negative"' in detail
    assert 'data-preparation-warning="hidden"' in detail

    searched = booking_list(client, when="all", q="B-0002").get_data(as_text=True)
    assert "B-0002" in searched
    assert "B-0001" not in searched


def test_exact_code_lookup_finds_booking_without_exposing_code(
    app, client: FlaskClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client, "2026-09-10")
    logger = logging.getLogger("venue_inventory.admin")
    prior_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        response = lookup_booking(client, code.lower().replace("-", " "))
        output = capsys.readouterr().out
    finally:
        logger.setLevel(prior_level)

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/bookings/1")
    assert response.request.path == "/admin/bookings/lookup"
    assert response.request.query_string == b""
    assert "access_code" not in response.headers["Location"]
    assert code not in response.headers["Location"]
    assert code not in output
    assert normalize_access_code(code) not in output

    detail = client.get("/admin/bookings/1")
    body = detail.get_data(as_text=True)
    assert code not in body
    assert normalize_access_code(code) not in body
    assert CODE_RE.search(body) is None

    failed = lookup_booking(client, "AAAA-AAAA-AAAA")
    failed_body = failed.get_data(as_text=True)
    assert failed.status_code == 200
    assert "No booking matches that access code." in failed_body
    assert "AAAA-AAAA-AAAA" not in failed_body
    assert 'value="AAAA' not in failed_body


def test_admin_event_date_edit_preserves_customer_access(
    app, client: FlaskClient, clock
) -> None:
    assert sign_in(client).status_code == 302
    chicago_today = clock.now().astimezone(ZoneInfo("America/Chicago")).date()
    code = create_booking(client, (chicago_today + timedelta(days=3)).isoformat())
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302
    assert customer.get("/customer/portal").status_code == 200

    past = (chicago_today - timedelta(days=5)).isoformat()
    response = update_event_date(client, 1, past)
    assert response.status_code == 302
    detail = client.get("/admin/bookings/1").get_data(as_text=True)
    assert past in detail
    assert customer.get("/customer/portal").status_code == 200

    future = (chicago_today + timedelta(days=10)).isoformat()
    assert update_event_date(client, 1, future).status_code == 302
    assert customer.get("/customer/portal").status_code == 200
    with app.app_context():
        booking = get_session().get(Booking, 1)
        assert booking is not None
        assert booking.event_date.isoformat() == future


def test_booking_delete_confirmation_shows_reference_date_and_selection_count(
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
    code = create_booking(client, "2026-07-01")
    assert admin_save(client, 1, 1, "2").status_code == 302
    assert admin_save(client, 1, 2, "1").status_code == 302
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302

    confirm = client.get("/admin/bookings/1/delete")
    body = confirm.get_data(as_text=True)
    assert confirm.status_code == 200
    assert "B-0001" in body
    assert "2026-07-01" in body
    assert "2 item types" in body
    assert "customer name" not in body.lower()
    assert "fulfillment" not in body.lower()

    response = client.post(
        "/admin/bookings/1/delete",
        data={"csrf_token": csrf_token(confirm)},
    )
    assert response.status_code == 302
    assert customer.get("/customer/portal").status_code == 302
    with app.app_context():
        assert get_session().get(Booking, 1) is None
        assert get_session().execute(select(BookingSelection)).scalars().all() == []
        assert (
            get_session()
            .execute(select(WebSession).where(WebSession.actor_type == "booking"))
            .scalars()
            .all()
            == []
        )


def test_booking_detail_exposes_print_classes_and_preparation_fields(
    client: FlaskClient,
) -> None:
    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="2").status_code
        == 302
    )
    create_booking(client, "2026-09-15")
    assert admin_save(client, 1, 1, "3").status_code == 302

    detail = client.get("/admin/bookings/1").get_data(as_text=True)
    assert 'data-print-list' in detail
    assert 'data-preparation-list' in detail
    assert 'data-preparation-reference' in detail
    assert 'data-preparation-event-date' in detail
    assert 'data-preparation-item' in detail
    assert "no-print" in detail
    css_path = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "app.css"
    assert "@media print" in css_path.read_text(encoding="utf-8")
    assert "fulfillment" not in detail.lower()
    assert "audit" not in detail.lower()


@pytest.mark.browser
def test_real_browser_admin_quantity_edit_and_print_media(
    app, client: FlaskClient
) -> None:
    node = shutil.which("node")
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if node is None or chrome is None:
        pytest.skip("Node and Chrome are required for the real-browser smoke test.")

    assert sign_in(client).status_code == 302
    assert (
        submit_item(client, "/admin/items", name="Chair", quantity="5").status_code
        == 302
    )
    create_booking(client, "2026-09-20")
    assert admin_save(client, 1, 1, "2").status_code == 302

    try:
        server = make_server("127.0.0.1", 0, app, threaded=True)
    except PermissionError:
        pytest.skip("The sandbox does not permit the browser smoke HTTP socket.")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    script = Path(__file__).with_name("admin_booking_smoke.mjs")
    try:
        completed = subprocess.run(
            [
                node,
                str(script),
                f"http://127.0.0.1:{server.server_port}",
                TEST_PASSWORD,
                "1",
            ],
            env={"CHROME_EXECUTABLE": chrome},
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["outcome"] == "passed"
    assert result["quantity"] == 4
    assert result["print_visible"] is True
    assert result["controls_hidden"] is True
