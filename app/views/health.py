from __future__ import annotations

import logging

from flask import Blueprint, current_app
from sqlalchemy import text

from app.data_dir import data_dir_is_ready, database_file_exists
from app.migrate import current_revision, head_revision

bp = Blueprint("health", __name__)
logger = logging.getLogger("venue_inventory.health")

_PLAIN = {"Content-Type": "text/plain; charset=utf-8"}


@bp.get("/healthz")
def liveness() -> tuple[str, int, dict[str, str]]:
    return "ok\n", 200, _PLAIN


@bp.get("/readyz")
def readiness() -> tuple[str, int, dict[str, str]]:
    config = current_app.config["APP_CONFIG"]
    ready, reason = data_dir_is_ready(config)
    if not ready:
        logger.warning(
            "Readiness check failed.",
            extra={"event": "readyz_failed", "reason": reason},
        )
        return "not ready\n", 503, _PLAIN
    if not database_file_exists(config):
        logger.warning(
            "Readiness check failed.",
            extra={"event": "readyz_failed", "reason": "database_missing"},
        )
        return "not ready\n", 503, _PLAIN
    engine = current_app.extensions["engine"]
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revision = current_revision(engine)
    except Exception:
        logger.warning(
            "Readiness check failed.",
            extra={"event": "readyz_failed", "reason": "database_unavailable"},
            exc_info=True,
        )
        return "not ready\n", 503, _PLAIN
    expected = head_revision()
    if revision != expected:
        logger.warning(
            "Readiness check failed.",
            extra={
                "event": "readyz_failed",
                "reason": "schema_not_at_head",
                "revision": revision or "none",
            },
        )
        return "not ready\n", 503, _PLAIN
    return "ok\n", 200, _PLAIN
