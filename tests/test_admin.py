from __future__ import annotations

from datetime import timedelta

from app import create_app
from app.clock import FrozenClock
from app.config import AppConfig
from app.db import get_session
from app.migrate import upgrade_to_head
from app.models import WebSession
from app.rate_limit import MAX_FAILED_ATTEMPTS, MemoryRateLimitStore
from app.security import SESSION_COOKIE_NAME
from flask.testing import FlaskClient
from sqlalchemy import select
from tests.conftest import TEST_HASH, TEST_PASSWORD, csrf_token, read_csrf, sign_in


def test_unauthenticated_dashboard_redirects(client: FlaskClient) -> None:
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_login_with_correct_password_creates_opaque_session(
    app, client: FlaskClient, app_config: AppConfig
) -> None:
    response = sign_in(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/")
    set_cookie = response.headers.get("Set-Cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie
    assert "Max-Age=43200" in set_cookie
    assert "Secure" not in set_cookie
    cookie = client.get_cookie(SESSION_COOKIE_NAME)
    assert cookie is not None
    assert cookie.value
    assert len(cookie.value) >= 32

    dashboard = client.get("/admin/")
    assert dashboard.status_code == 200
    body = dashboard.get_data(as_text=True)
    assert "Dashboard" in body
    assert "empty" in body.lower()

    raw_db = app_config.database_path.read_bytes()
    assert TEST_PASSWORD.encode() not in raw_db
    assert b"$argon2" not in raw_db
    assert cookie.value.encode() not in raw_db

    with app.app_context():
        rows = get_session().execute(select(WebSession)).scalars().all()
        assert len(rows) == 1
        assert rows[0].actor_type == "admin"
        assert rows[0].session_digest != cookie.value


def test_incorrect_password_is_generic(client: FlaskClient) -> None:
    response = sign_in(client, password="wrong-password")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Sign-in failed." in body
    assert "wrong-password" not in body
    assert "argon2" not in body.lower()
    assert client.get_cookie(SESSION_COOKIE_NAME) is None
    assert client.get("/admin/").status_code == 302


def test_throttled_attempts_use_the_same_generic_message(
    client: FlaskClient,
) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        response = sign_in(client, password="nope")
        assert response.status_code == 200
        assert "Sign-in failed." in response.get_data(as_text=True)

    blocked = sign_in(client, password=TEST_PASSWORD)
    assert blocked.status_code == 429
    body = blocked.get_data(as_text=True)
    assert "Sign-in failed." in body
    assert "argon2" not in body.lower()
    assert "too many" not in body.lower()
    assert client.get_cookie(SESSION_COOKIE_NAME) is None


def test_throttle_clears_after_window(client: FlaskClient, clock: FrozenClock) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert sign_in(client, password="nope").status_code == 200
    assert sign_in(client, password=TEST_PASSWORD).status_code == 429
    clock.advance(timedelta(minutes=15))
    response = sign_in(client, password=TEST_PASSWORD)
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200


def test_session_expires_after_twelve_hours(
    client: FlaskClient, clock: FrozenClock
) -> None:
    assert sign_in(client).status_code == 302
    clock.advance(timedelta(hours=12) - timedelta(seconds=1))
    assert client.get("/admin/").status_code == 200
    clock.advance(timedelta(seconds=1))
    response = client.get("/admin/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")


def test_logout_invalidates_session_with_csrf(app, client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    dashboard = client.get("/admin/")
    token = csrf_token(dashboard)
    response = client.post("/admin/logout", data={"csrf_token": token})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/login")
    assert client.get("/admin/").status_code == 302
    with app.app_context():
        rows = get_session().execute(select(WebSession)).scalars().all()
        assert rows == []


def test_logout_requires_post(client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    response = client.get("/admin/logout")
    assert response.status_code == 405
    assert client.get("/admin/").status_code == 200


def test_logout_without_csrf_is_rejected(app, client: FlaskClient) -> None:
    assert sign_in(client).status_code == 302
    response = client.post("/admin/logout", data={})
    assert response.status_code == 403
    assert "could not be verified" in response.get_data(as_text=True).lower()
    assert client.get("/admin/").status_code == 200
    with app.app_context():
        rows = get_session().execute(select(WebSession)).scalars().all()
        assert len(rows) == 1


def test_login_without_csrf_is_rejected(client: FlaskClient) -> None:
    response = client.post("/admin/login", data={"password": TEST_PASSWORD})
    assert response.status_code == 403
    assert client.get_cookie(SESSION_COOKIE_NAME) is None


def test_session_survives_app_recreation(
    app_config: AppConfig,
    clock: FrozenClock,
    rate_limit_store: MemoryRateLimitStore,
) -> None:
    first = create_app(app_config, clock=clock, rate_limit_store=rate_limit_store)
    upgrade_to_head(app_config.database_url)
    first_client = first.test_client()
    assert sign_in(first_client).status_code == 302
    cookie = first_client.get_cookie(SESSION_COOKIE_NAME)
    assert cookie is not None

    second = create_app(
        app_config, clock=clock, rate_limit_store=MemoryRateLimitStore()
    )
    second_client = second.test_client()
    second_client.set_cookie(SESSION_COOKIE_NAME, cookie.value)
    response = second_client.get("/admin/")
    assert response.status_code == 200
    assert "Dashboard" in response.get_data(as_text=True)


def test_home_page_is_responsive_and_accessible(client: FlaskClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'lang="en"' in body
    assert "viewport" in body
    assert "noindex" in body
    assert 'href="/admin/login"' in body
    assert "Skip to content" in body


def test_login_page_has_labeled_password_field(client: FlaskClient) -> None:
    response = client.get("/admin/login")
    body = response.get_data(as_text=True)
    assert 'for="password"' in body
    assert 'id="password"' in body
    assert 'autocomplete="current-password"' in body
    assert read_csrf(client)


def test_secure_cookie_flag_is_emitted_when_configured(
    data_dir, clock, rate_limit_store
) -> None:
    config = AppConfig(
        secret_key="local-test-secret-key-32-bytes-min",
        admin_password_hash=TEST_HASH,
        data_dir=data_dir,
        session_cookie_secure=True,
        trust_proxy=False,
        require_data_mount=False,
        log_level="WARNING",
    )
    application = create_app(config, clock=clock, rate_limit_store=rate_limit_store)
    upgrade_to_head(config.database_url)
    client = application.test_client()
    response = sign_in(client)
    assert "Secure" in response.headers.get("Set-Cookie", "")
