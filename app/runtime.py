from __future__ import annotations

import logging

from app.config import AppConfig, ConfigError
from app.data_dir import data_dir_is_ready
from app.logging import configure_logging
from app.migrate import upgrade_to_head

logger = logging.getLogger("venue_inventory")


def prepare_runtime(environ: dict[str, str] | None = None) -> None:
    config = AppConfig.from_environ(environ)
    configure_logging(config.log_level)
    ready, reason = data_dir_is_ready(config)
    if not ready:
        logger.error(
            "Persistent data directory is not ready; skipping migrations.",
            extra={
                "event": "data_dir_unready",
                "reason": reason,
                "data_dir": str(config.data_dir),
            },
        )
        return
    try:
        revision = upgrade_to_head(config.database_url)
    except Exception:
        configure_logging(config.log_level)
        logger.exception(
            "Database migration failed.",
            extra={"event": "migration_failed", "data_dir": str(config.data_dir)},
        )
        return
    configure_logging(config.log_level)
    logger.info(
        "Database schema is at head.",
        extra={"event": "migrations_applied", "revision": revision},
    )


def main() -> None:
    try:
        prepare_runtime()
    except ConfigError as exc:
        configure_logging("ERROR")
        logger.error(str(exc), extra={"event": "config_error"})
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
