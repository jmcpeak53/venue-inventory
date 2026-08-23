"""Cross-process coordination for database/image snapshots.

SQLite safely handles ordinary concurrent readers and writers.  A backup also
needs a matching image set, however, so image-reference changes take this lock
while the backup holds it for the SQLite copy and image collection.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_FILENAME = ".venue-inventory-snapshot.lock"


@contextmanager
def snapshot_lock(data_dir: Path) -> Iterator[None]:
    """Acquire the data-root lock used by backups and image mutations.

    The descriptor is intentionally opened read-only.  That lets an
    administrator-owned backup job create a mode-0644 lock file while the
    non-root application user can still later take an advisory ``flock``.
    """

    path = data_dir / LOCK_FILENAME
    descriptor = os.open(path, os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
