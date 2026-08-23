from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from app import create_app
from app.access_codes import (
    ACCESS_CODE_ALPHABET,
    access_code_digest,
    format_access_code,
    generate_access_code,
    is_valid_access_code,
    normalize_access_code,
)
from app.db import get_session
from app.migrate import upgrade_to_head
from app.models import Booking, WebSession
from app.rate_limit import FAILURE_WINDOW, MAX_FAILED_ATTEMPTS, MemoryRateLimitStore
from app.security import CUSTOMER_SESSION_SECONDS, SESSION_COOKIE_NAME
from flask.testing import FlaskClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from tests.conftest import csrf_token, sign_in

CODE_RE = re.compile(r"\b([A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4})\b")


def create_booking(client: FlaskClient, event_date: str = "2026-08-22") -> str:
    form = client.get("/admin/bookings/new")
    assert form.status_code == 200
    response = client.post(
        "/admin/bookings",
        data={"csrf_token": csrf_token(form), "event_date": event_date},
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    match = CODE_RE.search(response.get_data(as_text=True))
    assert match is not None
    return match.group(1)


def customer_login_token(client: FlaskClient) -> str:
    response = client.get("/customer/login")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    return csrf_token(response)


def customer_sign_in(client: FlaskClient, code: str, **request_kwargs):
    return client.post(
        "/customer/login",
        data={
            "csrf_token": customer_login_token(client),
            "access_code": code,
        },
        **request_kwargs,
    )


def test_access_code_generation_format_normalization_and_alphabet() -> None:
    ambiguous = set("ILO01")
    for _ in range(100):
        code = generate_access_code()
        assert len(code) == 12
        assert set(code) <= set(ACCESS_CODE_ALPHABET)
        assert not set(code) & ambiguous
        assert format_access_code(code) == f"{code[:4]}-{code[4:8]}-{code[8:]}"
        entered = f" \t{code[:4].lower()}-{code[4:8]} {code[8:]}\n"
        assert normalize_access_code(entered) == code
        assert is_valid_access_code(entered)

    assert not is_valid_access_code("B-0001")
    assert not is_valid_access_code("A" * 65)
    with pytest.raises(ValueError):
        format_access_code("too-short")


def test_admin_creates_booking_with_date_and_persists_only_digest(
    app, app_config, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client, "1900-01-01")
    created_body = client.get("/admin/bookings/1").get_data(as_text=True)
    list_body = client.get("/admin/bookings?when=all").get_data(as_text=True)
    assert code not in created_body
    assert code not in list_body
    assert "B-0001" in created_body
    assert "1900-01-01" in list_body
    assert "recover" not in created_body.lower()
    assert "regenerat" not in created_body.lower()

    with app.app_context():
        booking = get_session().get(Booking, 1)
        assert booking is not None
        assert booking.public_reference == "B-0001"
        assert booking.event_date.isoformat() == "1900-01-01"
        assert booking.revision == 0
        assert booking.access_code_digest == access_code_digest(
            code, app_config.access_code_hmac_secret
        )
        assert booking.access_code_digest != normalize_access_code(code)

        database_bytes = b"".join(
            path.read_bytes()
            for path in app_config.data_dir.glob("venue-inventory.sqlite3*")
        )
        assert normalize_access_code(code).encode() not in database_bytes
        assert code.encode() not in database_bytes
        assert app_config.access_code_hmac_secret.encode() not in database_bytes

        booking.public_reference = "B-9999"
        with pytest.raises(IntegrityError):
            get_session().commit()
        get_session().rollback()
        assert get_session().get(Booking, 1).public_reference == "B-0001"


def test_booking_form_accepts_past_present_and_future_chicago_dates(
    app, client: FlaskClient, clock
) -> None:
    assert sign_in(client).status_code == 302
    chicago_today = clock.now().astimezone(ZoneInfo("America/Chicago")).date()
    dates = (
        chicago_today - timedelta(days=1),
        chicago_today,
        chicago_today + timedelta(days=1),
    )
    codes = [create_booking(client, value.isoformat()) for value in dates]

    for code in codes:
        customer = app.test_client()
        assert customer_sign_in(customer, code).status_code == 302
        assert customer.get("/customer/portal").status_code == 200


def test_booking_creation_retries_a_digest_collision(
    monkeypatch: pytest.MonkeyPatch, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    generated = iter(
        ("ABCDEFGHJKMN", "ABCDEFGHJKMN", "PQRSTVWXYZ23")
    )
    monkeypatch.setattr(
        "app.views.admin.generate_access_code", lambda: next(generated)
    )
    assert create_booking(client) == "ABCD-EFGH-JKMN"
    assert create_booking(client, "2027-08-22") == "PQRS-TVWX-YZ23"


def test_normalized_code_login_creates_opaque_30_day_session(
    app, app_config, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    response = customer_sign_in(customer, code.lower().replace("-", " "))
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customer/portal")
    assert code not in response.headers["Location"]
    cookie_header = response.headers["Set-Cookie"]
    assert f"Max-Age={CUSTOMER_SESSION_SECONDS}" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=Lax" in cookie_header
    cookie = customer.get_cookie(SESSION_COOKIE_NAME)
    assert cookie is not None
    assert len(cookie.value) >= 32

    portal = customer.get("/customer/portal")
    assert portal.status_code == 200
    assert code not in portal.get_data(as_text=True)

    with app.app_context():
        booking_session = get_session().execute(
            select(WebSession).where(WebSession.actor_type == "booking")
        ).scalar_one()
        assert booking_session.booking_id == 1
        assert booking_session.session_digest != cookie.value
        database_bytes = b"".join(
            path.read_bytes()
            for path in app_config.data_dir.glob("venue-inventory.sqlite3*")
        )
        assert cookie.value.encode() not in database_bytes


def test_reference_bad_code_and_throttled_attempt_are_indistinguishable(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    responses = [customer_sign_in(customer, "B-0001")]
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        responses.append(customer_sign_in(customer, "AAAA-AAAA-AAAB"))
    responses.append(customer_sign_in(customer, code))

    assert all(response.status_code == 200 for response in responses)
    assert all(
        "Access code not recognized." in response.get_data(as_text=True)
        for response in responses
    )
    assert all(
        response.headers["Cache-Control"] == "no-store" for response in responses
    )
    assert customer.get_cookie(SESSION_COOKIE_NAME) is None


def test_customer_throttle_uses_trusted_remote_address(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    for index in range(MAX_FAILED_ATTEMPTS):
        response = customer_sign_in(
            customer,
            "AAAA-AAAA-AAAB",
            headers={"X-Forwarded-For": f"203.0.113.{index}"},
            environ_base={"REMOTE_ADDR": "10.0.0.8"},
        )
        assert response.status_code == 200
    blocked = customer_sign_in(
        customer, code, environ_base={"REMOTE_ADDR": "10.0.0.8"}
    )
    assert blocked.status_code == 200
    assert "Access code not recognized." in blocked.get_data(as_text=True)


def test_customer_throttle_clears_after_window(app, client, clock) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert customer_sign_in(customer, "AAAA-AAAA-AAAB").status_code == 200
    clock.advance(FAILURE_WINDOW)
    assert customer_sign_in(customer, code).status_code == 302


def test_submitted_code_is_absent_from_application_and_request_logs(
    app, client: FlaskClient, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    logger = logging.getLogger("venue_inventory.customer")
    prior_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        response = customer_sign_in(customer, code)
        output = capsys.readouterr().out
    finally:
        logger.setLevel(prior_level)

    assert response.status_code == 302
    assert response.request.path == "/customer/login"
    assert response.request.query_string == b""
    assert code not in output
    assert normalize_access_code(code) not in output


def test_customer_logout_expiry_and_csrf(app, client: FlaskClient, clock) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302

    assert customer.post("/customer/logout", data={}).status_code == 403
    assert customer.get("/customer/portal").status_code == 200
    portal = customer.get("/customer/portal")
    response = customer.post(
        "/customer/logout", data={"csrf_token": csrf_token(portal)}
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/customer/login")
    assert customer.get_cookie(SESSION_COOKIE_NAME) is None

    assert customer_sign_in(customer, code).status_code == 302
    clock.advance(timedelta(seconds=CUSTOMER_SESSION_SECONDS))
    assert customer.get("/customer/portal").status_code == 302


def test_booking_delete_is_csrf_protected_and_invalidates_session_and_code(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client, "2020-01-01")
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302

    assert client.post("/admin/bookings/1/delete", data={}).status_code == 403
    assert customer.get("/customer/portal").status_code == 200

    delete_page = client.get("/admin/bookings/1/delete")
    response = client.post(
        "/admin/bookings/1/delete",
        data={"csrf_token": csrf_token(delete_page)},
    )
    assert response.status_code == 302
    assert customer.get("/customer/portal").status_code == 302
    failed_login = customer_sign_in(customer, code)
    assert failed_login.status_code == 200
    assert "Access code not recognized." in failed_login.get_data(as_text=True)

    with app.app_context():
        assert get_session().get(Booking, 1) is None
        assert get_session().execute(
            select(WebSession).where(WebSession.actor_type == "booking")
        ).scalars().all() == []


def test_booking_delete_rolls_back_session_cleanup_if_booking_delete_fails(
    app, client: FlaskClient
) -> None:
    assert sign_in(client).status_code == 302
    code = create_booking(client)
    customer = app.test_client()
    assert customer_sign_in(customer, code).status_code == 302

    def fail_booking_commit(session: Session) -> None:
        if any(isinstance(row, Booking) for row in session.deleted):
            raise SQLAlchemyError("injected booking delete failure")

    event.listen(Session, "before_commit", fail_booking_commit)
    try:
        delete_page = client.get("/admin/bookings/1/delete")
        response = client.post(
            "/admin/bookings/1/delete",
            data={"csrf_token": csrf_token(delete_page)},
        )
    finally:
        event.remove(Session, "before_commit", fail_booking_commit)

    assert response.status_code == 200
    assert "could not be deleted" in response.get_data(as_text=True)
    assert customer.get("/customer/portal").status_code == 200
    with app.app_context():
        assert get_session().get(Booking, 1) is not None
        assert get_session().execute(
            select(WebSession).where(WebSession.actor_type == "booking")
        ).scalar_one().booking_id == 1


def test_customer_cookie_is_secure_when_configured(
    app_config, clock
) -> None:
    secure_config = replace(app_config, session_cookie_secure=True)
    application = create_app(
        secure_config,
        clock=clock,
        rate_limit_store=MemoryRateLimitStore(),
    )
    upgrade_to_head(secure_config.database_url)
    admin = application.test_client()
    assert sign_in(admin).status_code == 302
    code = create_booking(admin)
    customer = application.test_client()
    response = customer_sign_in(customer, code)
    assert response.status_code == 302
    assert "Secure" in response.headers["Set-Cookie"]
