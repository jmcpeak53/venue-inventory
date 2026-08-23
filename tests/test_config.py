from __future__ import annotations

from pathlib import Path

import pytest
from app import create_app
from app.config import AppConfig, ConfigError
from argon2 import PasswordHasher
from argon2.low_level import Type
from tests.conftest import TEST_HASH


def _valid_env(data_dir: Path) -> dict[str, str]:
    return {
        "VENUE_INVENTORY_SECRET_KEY": "local-test-secret-key-32-bytes-min",
        "VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET": (
            "local-test-access-code-hmac-secret-32"
        ),
        "VENUE_INVENTORY_ADMIN_PASSWORD_HASH": TEST_HASH,
        "VENUE_INVENTORY_DATA_DIR": str(data_dir),
        "VENUE_INVENTORY_SESSION_COOKIE_SECURE": "false",
        "VENUE_INVENTORY_REQUIRE_DATA_MOUNT": "false",
    }


def test_from_environ_accepts_complete_settings(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    config = AppConfig.from_environ(_valid_env(data_dir))
    assert config.data_dir == data_dir
    assert config.session_cookie_secure is False
    assert config.database_path == data_dir / "venue-inventory.sqlite3"


def test_missing_secret_key_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    del env["VENUE_INVENTORY_SECRET_KEY"]
    with pytest.raises(ConfigError, match="VENUE_INVENTORY_SECRET_KEY"):
        AppConfig.from_environ(env)


def test_short_secret_key_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    env["VENUE_INVENTORY_SECRET_KEY"] = "too-short"
    with pytest.raises(ConfigError, match="at least 32"):
        AppConfig.from_environ(env)


def test_missing_access_code_hmac_secret_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    del env["VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET"]
    with pytest.raises(ConfigError, match="ACCESS_CODE_HMAC_SECRET"):
        AppConfig.from_environ(env)


def test_short_access_code_hmac_secret_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    env["VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET"] = "too-short"
    with pytest.raises(ConfigError, match="ACCESS_CODE_HMAC_SECRET.*at least 32"):
        AppConfig.from_environ(env)


def test_missing_admin_hash_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    del env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH"]
    with pytest.raises(ConfigError, match="VENUE_INVENTORY_ADMIN_PASSWORD_HASH"):
        AppConfig.from_environ(env)


def test_admin_hash_file_is_accepted(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    hash_file = tmp_path / "admin-password.hash"
    hash_file.write_text(TEST_HASH, encoding="utf-8")
    env = _valid_env(data_dir)
    del env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH"]
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE"] = str(hash_file)
    config = AppConfig.from_environ(env)
    assert config.admin_password_hash == TEST_HASH


def test_empty_hash_file_env_uses_inline_hash(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE"] = ""
    config = AppConfig.from_environ(env)
    assert config.admin_password_hash == TEST_HASH


def test_admin_hash_file_overrides_inline_hash(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    hash_file = tmp_path / "admin-password.hash"
    hash_file.write_text(TEST_HASH, encoding="utf-8")
    env = _valid_env(data_dir)
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH"] = PasswordHasher(
        time_cost=1, memory_cost=8, parallelism=1
    ).hash("other-password")
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE"] = str(hash_file)
    config = AppConfig.from_environ(env)
    assert config.admin_password_hash == TEST_HASH


def test_missing_admin_hash_file_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    del env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH"]
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE"] = str(tmp_path / "missing.hash")
    with pytest.raises(ConfigError, match="ADMIN_PASSWORD_HASH_FILE"):
        AppConfig.from_environ(env)


def test_malformed_admin_hash_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH"] = "not-a-hash"
    with pytest.raises(ConfigError, match="Argon2id"):
        AppConfig.from_environ(env)


def test_non_id_argon2_hash_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    env["VENUE_INVENTORY_ADMIN_PASSWORD_HASH"] = PasswordHasher(
        time_cost=1, memory_cost=8, parallelism=1, type=Type.I
    ).hash("x")
    with pytest.raises(ConfigError, match="Argon2id"):
        AppConfig.from_environ(env)


def test_relative_data_dir_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    env["VENUE_INVENTORY_DATA_DIR"] = "relative/data"
    with pytest.raises(ConfigError, match="absolute path"):
        AppConfig.from_environ(env)


def test_missing_data_dir_is_rejected(tmp_path: Path) -> None:
    env = _valid_env(tmp_path / "data")
    del env["VENUE_INVENTORY_DATA_DIR"]
    with pytest.raises(ConfigError, match="VENUE_INVENTORY_DATA_DIR"):
        AppConfig.from_environ(env)


def test_create_app_refuses_missing_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "VENUE_INVENTORY_SECRET_KEY",
        "VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET",
        "VENUE_INVENTORY_ADMIN_PASSWORD_HASH",
        "VENUE_INVENTORY_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ConfigError):
        create_app()
