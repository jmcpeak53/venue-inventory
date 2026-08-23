"""Verified, application-aware backup and restore commands.

The archive deliberately contains only durable application state: a SQLite
online-backup snapshot and the normalized images referenced by that snapshot.
It never copies SQLite WAL files, temporary uploads, locks, or unreferenced
image files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from app.images import image_directory, is_generated_image_filename
from app.snapshot_lock import snapshot_lock

ARCHIVE_PREFIX = "venue-inventory-"
ARCHIVE_SUFFIX = ".tar.gz"
ARCHIVE_PATTERN = re.compile(
    r"^venue-inventory-\d{8}T\d{6}Z(?:-\d+)?\.tar\.gz$"
)
FORMAT_VERSION = 1
DATABASE_ARCHIVE_PATH = "database/venue-inventory.sqlite3"
MANIFEST_PATH = "manifest.json"
CHECKSUMS_PATH = "checksums.sha256"
GIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")
CHUNK_SIZE = 1048576


class BackupError(RuntimeError):
    """Raised when a backup cannot be safely created, verified, or restored."""


@dataclass(frozen=True)
class ImageEntry:
    filename: str
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class BackupManifest:
    created_at: str
    git_sha: str
    schema_revision: str
    database_sha256: str
    database_bytes: int
    images: tuple[ImageEntry, ...]

    @property
    def archive_paths(self) -> set[str]:
        return {
            DATABASE_ARCHIVE_PATH,
            MANIFEST_PATH,
            CHECKSUMS_PATH,
            *(entry.path for entry in self.images),
        }


def create_backup(
    *,
    data_dir: Path,
    backup_root: Path,
    git_sha: str,
    now: datetime | None = None,
) -> Path:
    """Create, verify, then atomically publish one timestamped archive."""

    data_dir = _require_absolute_directory(data_dir, "data directory", create=False)
    if data_dir == Path("/"):
        raise BackupError("The data directory cannot be the filesystem root.")
    _require_git_sha(git_sha)
    backup_root = _require_absolute_directory(backup_root, "backup root", create=True)
    if backup_root == data_dir:
        raise BackupError("The backup root must be separate from the data directory.")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    if not (data_dir / "venue-inventory.sqlite3").is_file():
        raise BackupError("The application database does not exist.")

    temporary_archive: Path | None = None
    staging_path: Path | None = None
    published: Path | None = None
    committed = False
    try:
        with snapshot_lock(data_dir):
            # Claim the published name while the lock is held so a second
            # backup that started in the same second cannot reuse it after
            # this snapshot is released and before os.replace runs.
            published = _reserve_next_archive(backup_root, timestamp)
            temporary_archive = backup_root / f".{published.name}.tmp-{os.getpid()}"
            staging_path = Path(
                tempfile.mkdtemp(prefix=".backup-stage-", dir=backup_root)
            )
            database_stage = staging_path / DATABASE_ARCHIVE_PATH
            database_stage.parent.mkdir(parents=True)
            _sqlite_online_backup(
                data_dir / "venue-inventory.sqlite3", database_stage
            )
            _validate_sqlite(database_stage)
            revision = _database_revision(database_stage)
            images = _copy_referenced_images(
                database_stage=database_stage,
                source_data_dir=data_dir,
                staging_path=staging_path,
            )
            manifest = BackupManifest(
                created_at=timestamp.isoformat().replace("+00:00", "Z"),
                git_sha=git_sha,
                schema_revision=revision,
                database_sha256=_sha256_path(database_stage),
                database_bytes=database_stage.stat().st_size,
                images=tuple(images),
            )
            _write_manifest_and_checksums(staging_path, manifest)
            _write_archive(staging_path, temporary_archive, manifest)
            os.chmod(temporary_archive, 0o600)
        if temporary_archive is None or published is None:
            raise BackupError("Backup could not be created safely: archive path missing.")
        verify_backup(temporary_archive)
        os.replace(temporary_archive, published)
        committed = True
        _fsync_directory(backup_root)
        return published
    except BackupError:
        raise
    except (OSError, sqlite3.Error, tarfile.TarError, ValueError) as exc:
        raise BackupError(f"Backup could not be created safely: {exc}") from exc
    finally:
        if staging_path is not None:
            shutil.rmtree(staging_path, ignore_errors=True)
        if temporary_archive is not None:
            Path(temporary_archive).unlink(missing_ok=True)
        if published is not None and not committed:
            published.unlink(missing_ok=True)


def verify_backup(archive: Path) -> BackupManifest:
    """Fail closed unless an archive is complete, internally consistent, and sane."""

    archive = archive.resolve()
    if not archive.is_file():
        raise BackupError("Backup archive does not exist or is not a regular file.")
    try:
        with tarfile.open(archive, mode="r:gz") as handle:
            members = handle.getmembers()
            _validate_tar_members(members)
            names = {member.name for member in members}
            manifest_member = _member_by_name(members, MANIFEST_PATH)
            checksums_member = _member_by_name(members, CHECKSUMS_PATH)
            manifest = _parse_manifest(_read_member(handle, manifest_member))
            if names != manifest.archive_paths:
                raise BackupError(
                    "Backup archive contains missing or unexpected files."
                )
            checksums = _parse_checksums(_read_member(handle, checksums_member))
            expected_checksums = {
                DATABASE_ARCHIVE_PATH: manifest.database_sha256,
                **{entry.path: entry.sha256 for entry in manifest.images},
            }
            if checksums != expected_checksums:
                raise BackupError(
                    "Backup checksum listing does not match its manifest."
                )
            with tempfile.TemporaryDirectory(
                prefix="venue-inventory-backup-verify-"
            ) as temp:
                database_copy = Path(temp) / "database.sqlite3"
                for member in members:
                    if member.name in {MANIFEST_PATH, CHECKSUMS_PATH}:
                        continue
                    digest, byte_count = _hash_member(handle, member, database_copy)
                    expected_digest = expected_checksums[member.name]
                    expected_size = (
                        manifest.database_bytes
                        if member.name == DATABASE_ARCHIVE_PATH
                        else next(
                            entry.bytes
                            for entry in manifest.images
                            if entry.path == member.name
                        )
                    )
                    if digest != expected_digest or byte_count != expected_size:
                        raise BackupError(
                            f"Checksum or size mismatch for {member.name}."
                        )
                _validate_sqlite(database_copy)
                if _database_revision(database_copy) != manifest.schema_revision:
                    raise BackupError(
                        "Backup schema revision does not match its manifest."
                    )
        return manifest
    except BackupError:
        raise
    except (OSError, sqlite3.Error, tarfile.TarError, ValueError) as exc:
        raise BackupError(f"Backup verification failed: {exc}") from exc


def restore_backup(
    *,
    archive: Path,
    data_dir: Path,
    owner: tuple[int, int] | None = None,
    require_empty_target: bool = False,
) -> Path:
    """Stage and apply an archive, preserving current state under a prior path.

    Verification occurs before a destination path is created or changed.  The
    returned directory is the recoverable prior database/images staging path.
    """

    manifest = verify_backup(archive)
    data_dir = _require_absolute_directory(data_dir, "data directory", create=True)
    if data_dir == Path("/"):
        raise BackupError("The data directory cannot be the filesystem root.")
    if require_empty_target and any(data_dir.iterdir()):
        raise BackupError("An isolated restore target must be empty.")

    stage: Path | None = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".restore-stage-", dir=data_dir))
        _extract_verified_state(archive, stage, manifest)
        _validate_staged_restore(stage, manifest)
        if owner is not None:
            _apply_ownership(stage, owner)
        with snapshot_lock(data_dir):
            prior = _apply_staged_restore(stage, data_dir)
        return prior
    except BackupError:
        raise
    except (OSError, tarfile.TarError, sqlite3.Error, ValueError) as exc:
        raise BackupError(f"Restore could not be applied safely: {exc}") from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def rollback_restore(
    *,
    data_dir: Path,
    prior_name: str,
    owner: tuple[int, int] | None = None,
) -> None:
    """Return a live destination to the state preserved by ``restore_backup``."""

    data_dir = _require_absolute_directory(data_dir, "data directory", create=False)
    if data_dir == Path("/"):
        raise BackupError("The data directory cannot be the filesystem root.")
    if re.fullmatch(r"\.restore-prior-[0-9TZ-]+(?:-\d+)?", prior_name) is None:
        raise BackupError("The prior restore directory name is invalid.")
    prior = data_dir / prior_name
    if not prior.is_dir():
        raise BackupError("The preserved prior restore directory does not exist.")
    if not (prior / "venue-inventory.sqlite3").is_file():
        raise BackupError("The preserved prior restore directory is incomplete.")

    with snapshot_lock(data_dir):
        rollback_stage = (
            data_dir / f".restore-failed-{_timestamp_token(datetime.now(UTC))}"
        )
        rollback_stage.mkdir()
        moved_current: list[str] = []
        try:
            for name in _state_names(data_dir):
                current = data_dir / name
                if current.exists():
                    os.replace(current, rollback_stage / name)
                    moved_current.append(name)
            for name in _state_names(data_dir):
                candidate = prior / name
                if candidate.exists():
                    os.replace(candidate, data_dir / name)
            if owner is not None:
                _apply_ownership_paths(
                    [data_dir / name for name in _state_names(data_dir)],
                    owner,
                )
            _fsync_directory(data_dir)
            shutil.rmtree(prior, ignore_errors=True)
        except Exception:
            for name in _state_names(data_dir):
                current = data_dir / name
                if current.exists():
                    os.replace(current, prior / name)
            for name in moved_current:
                original = rollback_stage / name
                if original.exists():
                    os.replace(original, data_dir / name)
            raise
        shutil.rmtree(rollback_stage, ignore_errors=True)


def run_restore_drill(
    *,
    archive: Path,
    target_data_dir: Path,
    owner: tuple[int, int] | None = None,
) -> Path:
    """Restore only into a new, explicitly empty directory and re-verify it."""

    if not target_data_dir.is_absolute():
        raise BackupError("The isolated restore target must be an absolute path.")
    target_data_dir = target_data_dir.resolve()
    if target_data_dir == Path("/"):
        raise BackupError("The isolated restore target cannot be the filesystem root.")
    target_data_dir.mkdir(parents=True, exist_ok=True)
    if any(target_data_dir.iterdir()):
        raise BackupError("The isolated restore target must be empty.")
    prior = restore_backup(
        archive=archive,
        data_dir=target_data_dir,
        owner=owner,
        require_empty_target=True,
    )
    if any((prior / name).exists() for name in _state_names(prior)):
        raise BackupError("An isolated restore unexpectedly found prior live state.")
    shutil.rmtree(prior, ignore_errors=True)
    manifest = verify_backup(archive)
    _validate_restored_data(target_data_dir, manifest)
    return target_data_dir


def prune_backups(
    *,
    backup_root: Path,
    retention_days: int = 14,
    now: datetime | None = None,
) -> list[Path]:
    """Remove only recognized backup archives older than the retention cutoff."""

    if retention_days < 0:
        raise BackupError("Retention days must be zero or greater.")
    backup_root = _require_absolute_directory(backup_root, "backup root", create=False)
    cutoff = (now or datetime.now(UTC)).astimezone(UTC) - timedelta(days=retention_days)
    removed: list[Path] = []
    for candidate in backup_root.iterdir():
        if ARCHIVE_PATTERN.fullmatch(candidate.name) is None:
            continue
        if not candidate.is_file() or candidate.is_symlink():
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
        if modified < cutoff:
            candidate.unlink()
            removed.append(candidate)
    return removed


def _copy_referenced_images(
    *,
    database_stage: Path,
    source_data_dir: Path,
    staging_path: Path,
) -> list[ImageEntry]:
    filenames = _referenced_image_filenames(database_stage)
    destination_dir = staging_path / "images"
    destination_dir.mkdir()
    entries: list[ImageEntry] = []
    for filename in filenames:
        source = image_directory(source_data_dir) / filename
        if not source.is_file() or source.is_symlink():
            raise BackupError(f"Referenced image is missing or unsafe: {filename}")
        destination = destination_dir / filename
        shutil.copyfile(source, destination)
        _fsync_file(destination)
        entries.append(
            ImageEntry(
                filename=filename,
                path=f"images/{filename}",
                sha256=_sha256_path(destination),
                bytes=destination.stat().st_size,
            )
        )
    return entries


def _referenced_image_filenames(database: Path) -> list[str]:
    try:
        with _read_only_sqlite(database) as connection:
            rows = connection.execute(
                "SELECT image_filename FROM inventory_items "
                "WHERE image_filename IS NOT NULL ORDER BY image_filename"
            ).fetchall()
    except sqlite3.Error as exc:
        raise BackupError(
            "Could not read referenced image filenames from database."
        ) from exc
    filenames = [row[0] for row in rows]
    if len(filenames) != len(set(filenames)):
        raise BackupError("The database references an image filename more than once.")
    if any(not is_generated_image_filename(name) for name in filenames):
        raise BackupError("The database contains an invalid normalized image filename.")
    return filenames


def _sqlite_online_backup(source_path: Path, destination_path: Path) -> None:
    with _read_only_sqlite(source_path) as source:
        destination = sqlite3.connect(destination_path)
        try:
            source.backup(destination)
        finally:
            destination.close()
    _fsync_file(destination_path)


@contextmanager
def _read_only_sqlite(path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA busy_timeout=5000")
        yield connection
    finally:
        connection.close()


def _database_revision(database: Path) -> str:
    try:
        with _read_only_sqlite(database) as connection:
            rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.Error as exc:
        raise BackupError(
            "Database schema revision metadata is missing or unreadable."
        ) from exc
    if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0]:
        raise BackupError("Database schema revision metadata is invalid.")
    return rows[0][0]


def _validate_sqlite(database: Path) -> None:
    try:
        with _read_only_sqlite(database) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    except sqlite3.Error as exc:
        raise BackupError("Database snapshot is unreadable.") from exc
    if integrity is None or integrity[0] != "ok":
        raise BackupError("Database integrity check failed.")
    if foreign_keys:
        raise BackupError("Database foreign-key check failed.")


def _write_manifest_and_checksums(stage: Path, manifest: BackupManifest) -> None:
    payload = {
        "format_version": FORMAT_VERSION,
        "created_at": manifest.created_at,
        "git_sha": manifest.git_sha,
        "schema_revision": manifest.schema_revision,
        "database": {
            "path": DATABASE_ARCHIVE_PATH,
            "sha256": manifest.database_sha256,
            "bytes": manifest.database_bytes,
        },
        "images": [
            {
                "filename": entry.filename,
                "path": entry.path,
                "sha256": entry.sha256,
                "bytes": entry.bytes,
            }
            for entry in manifest.images
        ],
    }
    _write_bytes(
        stage / MANIFEST_PATH,
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
    )
    checksums = "\n".join(
        [f"{manifest.database_sha256}  {DATABASE_ARCHIVE_PATH}"]
        + [f"{entry.sha256}  {entry.path}" for entry in manifest.images]
    )
    _write_bytes(stage / CHECKSUMS_PATH, (checksums + "\n").encode("ascii"))


def _write_archive(stage: Path, destination: Path, manifest: BackupManifest) -> None:
    paths = [
        DATABASE_ARCHIVE_PATH,
        *[entry.path for entry in manifest.images],
        MANIFEST_PATH,
        CHECKSUMS_PATH,
    ]
    with tarfile.open(destination, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for relative in paths:
            archive.add(stage / relative, arcname=relative)


def _validate_tar_members(members: list[tarfile.TarInfo]) -> None:
    if not members:
        raise BackupError("Backup archive is empty.")
    names: set[str] = set()
    for member in members:
        path = PurePosixPath(member.name)
        if (
            member.name in names
            or path.is_absolute()
            or ".." in path.parts
            or member.name != path.as_posix()
            or not member.isfile()
        ):
            raise BackupError("Backup archive contains an unsafe member.")
        names.add(member.name)


def _member_by_name(members: Iterable[tarfile.TarInfo], name: str) -> tarfile.TarInfo:
    for member in members:
        if member.name == name:
            return member
    raise BackupError(f"Backup archive is missing {name}.")


def _read_member(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    source = archive.extractfile(member)
    if source is None:
        raise BackupError(f"Backup archive member cannot be read: {member.name}")
    with source:
        return source.read()


def _hash_member(
    archive: tarfile.TarFile, member: tarfile.TarInfo, database_copy: Path
) -> tuple[str, int]:
    source = archive.extractfile(member)
    if source is None:
        raise BackupError(f"Backup archive member cannot be read: {member.name}")
    digest = hashlib.sha256()
    total = 0
    destination = (
        database_copy.open("wb") if member.name == DATABASE_ARCHIVE_PATH else None
    )
    try:
        with source:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if destination is not None:
                    destination.write(chunk)
    finally:
        if destination is not None:
            destination.close()
    return digest.hexdigest(), total


def _parse_manifest(payload: bytes) -> BackupManifest:
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict) or value.get("format_version") != FORMAT_VERSION:
        raise BackupError("Backup manifest format is unsupported.")
    created_at = _require_string(value, "created_at")
    try:
        parsed_created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BackupError("Backup manifest timestamp is invalid.") from exc
    if parsed_created_at.tzinfo is None:
        raise BackupError("Backup manifest timestamp must include a timezone.")
    git_sha = _require_string(value, "git_sha")
    _require_git_sha(git_sha)
    revision = _require_string(value, "schema_revision")
    database = value.get("database")
    if not isinstance(database, dict) or database.get("path") != DATABASE_ARCHIVE_PATH:
        raise BackupError("Backup manifest database metadata is invalid.")
    database_sha = _require_sha256(database.get("sha256"))
    database_bytes = _require_bytes(database.get("bytes"))
    raw_images = value.get("images")
    if not isinstance(raw_images, list):
        raise BackupError("Backup manifest image metadata is invalid.")
    entries: list[ImageEntry] = []
    for raw in raw_images:
        if not isinstance(raw, dict):
            raise BackupError("Backup manifest image metadata is invalid.")
        filename = _require_string(raw, "filename")
        path = _require_string(raw, "path")
        if not is_generated_image_filename(filename) or path != f"images/{filename}":
            raise BackupError("Backup manifest contains an invalid image path.")
        entries.append(
            ImageEntry(
                filename=filename,
                path=path,
                sha256=_require_sha256(raw.get("sha256")),
                bytes=_require_bytes(raw.get("bytes")),
            )
        )
    if entries != sorted(entries, key=lambda entry: entry.filename):
        raise BackupError("Backup manifest images are not in a stable order.")
    if len({entry.filename for entry in entries}) != len(entries):
        raise BackupError("Backup manifest contains duplicate images.")
    return BackupManifest(
        created_at=created_at,
        git_sha=git_sha,
        schema_revision=revision,
        database_sha256=database_sha,
        database_bytes=database_bytes,
        images=tuple(entries),
    )


def _parse_checksums(payload: bytes) -> dict[str, str]:
    try:
        lines = payload.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise BackupError("Backup checksums are not ASCII.") from exc
    result: dict[str, str] = {}
    for line in lines:
        try:
            digest, path = line.split("  ")
        except ValueError as exc:
            raise BackupError("Backup checksum listing is invalid.") from exc
        result[path] = _require_sha256(digest)
    return result


def _extract_verified_state(
    archive: Path, stage: Path, manifest: BackupManifest
) -> None:
    (stage / "images").mkdir()
    with tarfile.open(archive, mode="r:gz") as handle:
        members = handle.getmembers()
        _validate_tar_members(members)
        if {member.name for member in members} != manifest.archive_paths:
            raise BackupError("Backup archive changed after verification.")
        for member in members:
            if member.name in {MANIFEST_PATH, CHECKSUMS_PATH}:
                continue
            destination = stage / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = handle.extractfile(member)
            if source is None:
                raise BackupError(
                    f"Backup archive member cannot be read: {member.name}"
                )
            with source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, length=CHUNK_SIZE)


def _validate_staged_restore(stage: Path, manifest: BackupManifest) -> None:
    database = stage / DATABASE_ARCHIVE_PATH
    if _sha256_path(database) != manifest.database_sha256:
        raise BackupError("Staged database checksum does not match backup manifest.")
    _validate_sqlite(database)
    if _database_revision(database) != manifest.schema_revision:
        raise BackupError(
            "Staged database schema revision does not match backup manifest."
        )
    for entry in manifest.images:
        path = stage / entry.path
        if not path.is_file() or path.is_symlink() or path.stat().st_size != entry.bytes:
            raise BackupError(f"Staged image is missing or invalid: {entry.filename}")
        if _sha256_path(path) != entry.sha256:
            raise BackupError(f"Staged image checksum does not match: {entry.filename}")
    staged_names = {entry.filename for entry in manifest.images}
    if set(_referenced_image_filenames(database)) != staged_names:
        raise BackupError("Staged image set does not match database references.")


def _apply_staged_restore(stage: Path, data_dir: Path) -> Path:
    source_database = stage / DATABASE_ARCHIVE_PATH
    source_images = stage / "images"
    if not source_database.is_file() or not source_images.is_dir():
        raise BackupError("Staged restore is incomplete.")
    prior = _next_prior_directory(data_dir)
    prior.mkdir()
    moved: list[Path] = []
    installed: list[Path] = []
    try:
        for name in _state_names(data_dir):
            current = data_dir / name
            if not current.exists():
                continue
            os.replace(current, prior / name)
            moved.append(Path(name))
        for source, destination_name in (
            (source_database, "venue-inventory.sqlite3"),
            (source_images, "images"),
        ):
            destination = data_dir / destination_name
            os.replace(source, destination)
            installed.append(Path(destination_name))
        _fsync_directory(data_dir)
        return prior
    except Exception:
        for name in installed:
            destination = data_dir / name
            if destination.exists():
                os.replace(destination, stage / name)
        for name in moved:
            original = prior / name
            if original.exists():
                os.replace(original, data_dir / name)
        raise


def _validate_restored_data(data_dir: Path, manifest: BackupManifest) -> None:
    database = data_dir / "venue-inventory.sqlite3"
    if _sha256_path(database) != manifest.database_sha256:
        raise BackupError("Isolated restored database bytes do not match the backup.")
    _validate_sqlite(database)
    if _database_revision(database) != manifest.schema_revision:
        raise BackupError(
            "Isolated restored schema revision does not match the backup."
        )
    for entry in manifest.images:
        image = image_directory(data_dir) / entry.filename
        if _sha256_path(image) != entry.sha256:
            raise BackupError("Isolated restored image bytes do not match the backup.")


def _state_names(data_dir: Path) -> tuple[str, ...]:
    del data_dir
    return (
        "venue-inventory.sqlite3",
        "venue-inventory.sqlite3-wal",
        "venue-inventory.sqlite3-shm",
        "images",
    )


def _next_prior_directory(data_dir: Path) -> Path:
    base = f".restore-prior-{_timestamp_token(datetime.now(UTC))}"
    candidate = data_dir / base
    number = 2
    while candidate.exists():
        candidate = data_dir / f"{base}-{number}"
        number += 1
    return candidate


def _apply_ownership(path: Path, owner: tuple[int, int]) -> None:
    _apply_ownership_paths([path], owner)


def _apply_ownership_paths(paths: Iterable[Path], owner: tuple[int, int]) -> None:
    uid, gid = owner
    for root in paths:
        if not root.exists():
            continue
        os.chown(root, uid, gid)
        if root.is_dir():
            for child in root.rglob("*"):
                if child.is_symlink():
                    raise BackupError("Restore staging contains a symbolic link.")
                os.chown(child, uid, gid)


def _next_archive_name(root: Path, timestamp: datetime) -> str:
    base = f"{ARCHIVE_PREFIX}{_timestamp_token(timestamp)}{ARCHIVE_SUFFIX}"
    candidate = root / base
    number = 2
    while candidate.exists():
        candidate = root / (
            f"{ARCHIVE_PREFIX}{_timestamp_token(timestamp)}-{number}{ARCHIVE_SUFFIX}"
        )
        number += 1
    return candidate.name


def _reserve_next_archive(root: Path, timestamp: datetime) -> Path:
    """Exclusively create the next timestamped archive path.

    Existence checks alone are not enough: two backups can observe the same
    unused name before either publishes. Creating the destination with
    ``O_EXCL`` claims it so a later ``os.replace`` cannot clobber another
    backup's just-published archive.
    """

    while True:
        candidate = root / _next_archive_name(root, timestamp)
        try:
            descriptor = os.open(
                candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            continue
        os.close(descriptor)
        return candidate


def _timestamp_token(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _write_bytes(path: Path, payload: bytes) -> None:
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_absolute_directory(path: Path, label: str, create: bool) -> Path:
    if not path.is_absolute():
        raise BackupError(f"The {label} must be an absolute path.")
    path = path.resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise BackupError(f"The {label} is not a directory.")
    return path


def _require_git_sha(value: str) -> None:
    if GIT_SHA_PATTERN.fullmatch(value) is None:
        raise BackupError("A deployed Git SHA is required for backups.")


def _require_sha256(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise BackupError("Backup manifest contains an invalid SHA-256 value.")
    return value


def _require_bytes(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise BackupError("Backup manifest contains an invalid byte count.")
    return value


def _require_string(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise BackupError(f"Backup manifest field {key} is invalid.")
    return result


def _resolve_git_sha(value: str | None) -> str:
    candidate = (
        value or os.environ.get("VENUE_INVENTORY_DEPLOYED_GIT_SHA") or ""
    ).strip()
    if not candidate:
        try:
            candidate = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise BackupError("A deployed Git SHA is required for backups.") from exc
    _require_git_sha(candidate)
    return candidate


def _parse_owner(value: str | None) -> tuple[int, int] | None:
    if value is None or value == "":
        return None
    match = re.fullmatch(r"(\d+):(\d+)", value)
    if match is None:
        raise BackupError("Owner must use numeric UID:GID format.")
    return int(match.group(1)), int(match.group(2))


def _path_argument(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("path must be absolute")
    return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Venue Inventory backup and restore")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="create and verify a backup archive")
    backup.add_argument("--data-dir", type=_path_argument, default=Path("/data"))
    backup.add_argument("--backup-root", type=_path_argument, default=Path("/backups"))
    backup.add_argument("--git-sha")
    verify = commands.add_parser("verify", help="verify a backup archive")
    verify.add_argument("archive", type=_path_argument)
    prune = commands.add_parser("prune", help="remove expired recognized backups")
    prune.add_argument("--backup-root", type=_path_argument, default=Path("/backups"))
    prune.add_argument("--retention-days", type=int, default=14)
    restore = commands.add_parser("restore", help="stage and restore a verified backup")
    restore.add_argument("--archive", type=_path_argument, required=True)
    restore.add_argument("--data-dir", type=_path_argument, default=Path("/data"))
    restore.add_argument("--owner")
    rollback = commands.add_parser("rollback", help="restore the preserved prior data")
    rollback.add_argument("--data-dir", type=_path_argument, default=Path("/data"))
    rollback.add_argument("--prior", required=True)
    rollback.add_argument("--owner")
    drill = commands.add_parser(
        "restore-drill", help="restore only to an empty isolated target"
    )
    drill.add_argument("--archive", type=_path_argument, required=True)
    drill.add_argument("--target-data-dir", type=_path_argument, required=True)
    drill.add_argument("--owner")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "backup":
            archive = create_backup(
                data_dir=args.data_dir,
                backup_root=args.backup_root,
                git_sha=_resolve_git_sha(args.git_sha),
            )
            print(archive)
        elif args.command == "verify":
            verify_backup(args.archive)
            print("verified")
        elif args.command == "prune":
            for path in prune_backups(
                backup_root=args.backup_root,
                retention_days=args.retention_days,
            ):
                print(path)
        elif args.command == "restore":
            prior = restore_backup(
                archive=args.archive,
                data_dir=args.data_dir,
                owner=_parse_owner(args.owner),
            )
            print(prior)
        elif args.command == "rollback":
            rollback_restore(
                data_dir=args.data_dir,
                prior_name=args.prior,
                owner=_parse_owner(args.owner),
            )
            print("rolled back")
        elif args.command == "restore-drill":
            target = run_restore_drill(
                archive=args.archive,
                target_data_dir=args.target_data_dir,
                owner=_parse_owner(args.owner),
            )
            print(target)
        else:
            return 2
    except BackupError as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
