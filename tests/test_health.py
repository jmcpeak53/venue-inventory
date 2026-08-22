from __future__ import annotations

import shutil
from pathlib import Path

from app.config import AppConfig
from app.security import CONTENT_SECURITY_POLICY
from sqlalchemy import text


def test_liveness_does_not_require_the_database(
    app, client, app_config: AppConfig
) -> None:
    app_config.database_path.unlink()
    for extra in (
        app_config.database_path.with_name(app_config.database_path.name + "-wal"),
        app_config.database_path.with_name(app_config.database_path.name + "-shm"),
    ):
        extra.unlink(missing_ok=True)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok\n"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY


def test_readiness_succeeds_when_data_and_schema_are_ready(client) -> None:
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok\n"


def test_readiness_ignores_session_cookie(client, monkeypatch) -> None:
    import app as app_module

    def fail_if_session_is_loaded():
        raise AssertionError("readiness must not load a web session")

    monkeypatch.setattr(app_module, "get_session", fail_if_session_is_loaded)
    client.set_cookie("venue_session", "stale-token")

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok\n"


def test_readiness_fails_when_database_file_is_missing(
    app, client, app_config: AppConfig
) -> None:
    app_config.database_path.unlink()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_data(as_text=True) == "not ready\n"


def test_readiness_fails_when_data_dir_is_not_writable(
    client, app_config: AppConfig
) -> None:
    # Occupy the write probe so the readiness check cannot create it. This
    # remains deterministic even when the test process is root.
    probe = app_config.data_dir / ".write-probe"
    probe.mkdir()
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_data(as_text=True) == "not ready\n"


def test_readiness_succeeds_when_write_probe_is_already_removed(
    client, monkeypatch
) -> None:
    original_write_text = Path.write_text

    def write_then_remove(self, *args, **kwargs):
        result = original_write_text(self, *args, **kwargs)
        if self.name == ".write-probe":
            self.unlink(missing_ok=True)
        return result

    monkeypatch.setattr(Path, "write_text", write_then_remove)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok\n"


def test_readiness_fails_when_data_dir_is_not_a_directory(
    client, app_config: AppConfig
) -> None:
    shutil.rmtree(app_config.data_dir)
    app_config.data_dir.write_text("not-a-directory", encoding="utf-8")
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_data(as_text=True) == "not ready\n"


def test_readiness_fails_when_schema_is_not_at_head(app, client) -> None:
    engine = app.extensions["engine"]
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'not_head'"))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.get_data(as_text=True) == "not ready\n"
