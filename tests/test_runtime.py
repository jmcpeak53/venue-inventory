from __future__ import annotations

from pathlib import Path

import pytest
from app.config import ConfigError
from app.runtime import prepare_runtime
from tests.conftest import TEST_HASH


def test_prepare_runtime_refuses_missing_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VENUE_INVENTORY_SECRET_KEY", raising=False)
    monkeypatch.delenv("VENUE_INVENTORY_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("VENUE_INVENTORY_DATA_DIR", raising=False)
    with pytest.raises(ConfigError):
        prepare_runtime({})


def test_prepare_runtime_logs_head_revision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    prepare_runtime(
        {
            "VENUE_INVENTORY_SECRET_KEY": "local-test-secret-key-32-bytes-min",
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH": TEST_HASH,
            "VENUE_INVENTORY_DATA_DIR": str(data_dir),
            "VENUE_INVENTORY_REQUIRE_DATA_MOUNT": "false",
            "VENUE_INVENTORY_SESSION_COOKIE_SECURE": "false",
            "VENUE_INVENTORY_LOG_LEVEL": "INFO",
        }
    )
    output = capsys.readouterr().out
    assert "migrations_applied" in output
    assert "0002_inventory_items" in output


def test_prepare_runtime_skips_migration_when_data_dir_missing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-data"
    prepare_runtime(
        {
            "VENUE_INVENTORY_SECRET_KEY": "local-test-secret-key-32-bytes-min",
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH": TEST_HASH,
            "VENUE_INVENTORY_DATA_DIR": str(missing),
            "VENUE_INVENTORY_REQUIRE_DATA_MOUNT": "false",
            "VENUE_INVENTORY_SESSION_COOKIE_SECURE": "false",
            "VENUE_INVENTORY_LOG_LEVEL": "ERROR",
        }
    )
    assert not missing.exists()
