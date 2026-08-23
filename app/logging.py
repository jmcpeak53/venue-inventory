from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", None)
        if event:
            payload["event"] = event
        revision = getattr(record, "revision", None)
        if revision:
            payload["revision"] = revision
        client_ip = getattr(record, "client_ip", None)
        if client_ip:
            payload["client_ip"] = client_ip
        reason = getattr(record, "reason", None)
        if reason:
            payload["reason"] = reason
        data_dir = getattr(record, "data_dir", None)
        if data_dir:
            payload["data_dir"] = data_dir
        image_filename = getattr(record, "image_filename", None)
        if image_filename:
            payload["image_filename"] = image_filename
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    logger = logging.getLogger("venue_inventory")
    logger.disabled = False
    logger.setLevel(level)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
