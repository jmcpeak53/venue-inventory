from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from argon2 import extract_parameters
from argon2.exceptions import InvalidHashError
from argon2.low_level import Type

REQUIRED_SECRET_LENGTH = 32


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        message = "Invalid configuration:\n" + "\n".join(
            f"- {item}" for item in problems
        )
        super().__init__(message)


@dataclass(frozen=True)
class AppConfig:
    secret_key: str
    access_code_hmac_secret: str
    admin_password_hash: str
    data_dir: Path
    session_cookie_secure: bool
    trust_proxy: bool
    require_data_mount: bool
    log_level: str

    @property
    def database_path(self) -> Path:
        return self.data_dir / "venue-inventory.sqlite3"

    @property
    def database_url(self) -> str:
        return "sqlite:///" + self.database_path.as_posix()

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> AppConfig:
        env = environ if environ is not None else os.environ
        problems: list[str] = []

        secret_key = (env.get("VENUE_INVENTORY_SECRET_KEY") or "").strip()
        if not secret_key:
            problems.append("VENUE_INVENTORY_SECRET_KEY is required.")
        elif len(secret_key) < REQUIRED_SECRET_LENGTH:
            problems.append(
                "VENUE_INVENTORY_SECRET_KEY must be at least "
                f"{REQUIRED_SECRET_LENGTH} characters."
            )

        access_code_hmac_secret = (
            env.get("VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET") or ""
        ).strip()
        if not access_code_hmac_secret:
            problems.append("VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET is required.")
        elif len(access_code_hmac_secret) < REQUIRED_SECRET_LENGTH:
            problems.append(
                "VENUE_INVENTORY_ACCESS_CODE_HMAC_SECRET must be at least "
                f"{REQUIRED_SECRET_LENGTH} characters."
            )

        admin_hash = ""
        try:
            admin_hash = load_admin_password_hash(env)
            _require_argon2id(admin_hash)
        except ValueError as exc:
            problems.append(str(exc))

        data_dir_raw = (env.get("VENUE_INVENTORY_DATA_DIR") or "").strip()
        data_dir: Path | None = None
        if not data_dir_raw:
            problems.append("VENUE_INVENTORY_DATA_DIR is required.")
        else:
            data_dir = Path(data_dir_raw)
            if not data_dir.is_absolute():
                problems.append("VENUE_INVENTORY_DATA_DIR must be an absolute path.")

        try:
            session_cookie_secure = parse_bool(
                env.get("VENUE_INVENTORY_SESSION_COOKIE_SECURE"),
                default=True,
                name="VENUE_INVENTORY_SESSION_COOKIE_SECURE",
            )
        except ValueError as exc:
            problems.append(str(exc))
            session_cookie_secure = True

        try:
            trust_proxy = parse_bool(
                env.get("VENUE_INVENTORY_TRUST_PROXY"),
                default=False,
                name="VENUE_INVENTORY_TRUST_PROXY",
            )
        except ValueError as exc:
            problems.append(str(exc))
            trust_proxy = False

        try:
            require_data_mount = parse_bool(
                env.get("VENUE_INVENTORY_REQUIRE_DATA_MOUNT"),
                default=False,
                name="VENUE_INVENTORY_REQUIRE_DATA_MOUNT",
            )
        except ValueError as exc:
            problems.append(str(exc))
            require_data_mount = False

        log_level = (env.get("VENUE_INVENTORY_LOG_LEVEL") or "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            problems.append("VENUE_INVENTORY_LOG_LEVEL must be a standard log level.")

        if problems or data_dir is None:
            raise ConfigError(problems)

        return cls(
            secret_key=secret_key,
            access_code_hmac_secret=access_code_hmac_secret,
            admin_password_hash=admin_hash,
            data_dir=data_dir,
            session_cookie_secure=session_cookie_secure,
            trust_proxy=trust_proxy,
            require_data_mount=require_data_mount,
            log_level=log_level,
        )


def load_admin_password_hash(env: Mapping[str, str]) -> str:
    hash_file = (env.get("VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE") or "").strip()
    inline_hash = (env.get("VENUE_INVENTORY_ADMIN_PASSWORD_HASH") or "").strip()
    if hash_file:
        path = Path(hash_file)
        if not path.is_file():
            raise ValueError(
                "VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE must point to an "
                "existing file."
            )
        try:
            encoded = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                "VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE could not be read."
            ) from exc
        if not encoded:
            raise ValueError("VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE is empty.")
        return encoded
    if not inline_hash:
        raise ValueError(
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH or "
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH_FILE is required."
        )
    return inline_hash


def parse_bool(value: str | None, *, default: bool, name: str) -> bool:
    if value is None or value.strip() == "":
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _require_argon2id(encoded_hash: str) -> None:
    if not encoded_hash.startswith("$argon2id$"):
        raise ValueError(
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH must be an Argon2id encoded hash."
        )
    try:
        parameters = extract_parameters(encoded_hash)
    except (InvalidHashError, ValueError) as exc:
        raise ValueError(
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH is not a valid Argon2id hash."
        ) from exc
    if parameters.type is not Type.ID:
        raise ValueError(
            "VENUE_INVENTORY_ADMIN_PASSWORD_HASH must use the Argon2id algorithm."
        )
