from __future__ import annotations

import os
from pathlib import Path

from app.config import AppConfig

IMAGES_DIRECTORY_NAME = "images"


def data_dir_is_ready(config: AppConfig) -> tuple[bool, str]:
    path = config.data_dir
    if not path.exists():
        return False, "missing"
    if not path.is_dir():
        return False, "not_a_directory"
    if config.require_data_mount and not path.is_mount():
        return False, "not_mounted"
    if not os.access(path, os.W_OK):
        return False, "not_writable"
    probe = path / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        return False, "not_writable"
    return True, "ok"


def database_file_exists(config: AppConfig) -> bool:
    path: Path = config.database_path
    return path.is_file()


def ensure_data_layout(config: AppConfig) -> None:
    """Create the durable subdirectories after the data root is validated.

    The deployed state layout is intentionally small and stable:
    ``/data/venue-inventory.sqlite3`` plus ``/data/images``.  Backup lock and
    restore staging files are dot-prefixed operational metadata, never state
    included in an archive.
    """

    if config.data_dir.is_dir():
        (config.data_dir / IMAGES_DIRECTORY_NAME).mkdir(exist_ok=True)
