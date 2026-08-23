from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from app import create_app
from app.clock import FrozenClock
from app.config import AppConfig
from app.migrate import upgrade_to_head
from app.rate_limit import MemoryRateLimitStore
from argon2 import PasswordHasher
from flask.testing import FlaskClient

TEST_PASSWORD = "correct-horse-battery-staple"
TEST_HASH = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(
    TEST_PASSWORD
)
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 22, 15, 0, tzinfo=UTC))


@pytest.fixture
def rate_limit_store() -> MemoryRateLimitStore:
    return MemoryRateLimitStore()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def app_config(data_dir: Path) -> AppConfig:
    return AppConfig(
        secret_key="local-test-secret-key-32-bytes-min",
        access_code_hmac_secret="local-test-access-code-hmac-secret-32",
        admin_password_hash=TEST_HASH,
        data_dir=data_dir,
        session_cookie_secure=False,
        trust_proxy=False,
        require_data_mount=False,
        log_level="WARNING",
    )


@pytest.fixture
def app(
    app_config: AppConfig, clock: FrozenClock, rate_limit_store: MemoryRateLimitStore
):
    application = create_app(
        app_config,
        clock=clock,
        rate_limit_store=rate_limit_store,
    )
    upgrade_to_head(app_config.database_url)
    return application


@pytest.fixture
def client(app) -> FlaskClient:
    return app.test_client()


def csrf_token(response) -> str:
    match = CSRF_RE.search(response.get_data(as_text=True))
    assert match is not None, response.get_data(as_text=True)
    return match.group(1)


def read_csrf(client: FlaskClient, path: str = "/admin/login") -> str:
    response = client.get(path)
    assert response.status_code == 200
    return csrf_token(response)


def sign_in(client: FlaskClient, password: str = TEST_PASSWORD, **request_kwargs):
    token = read_csrf(client)
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "password": password},
        **request_kwargs,
    )
