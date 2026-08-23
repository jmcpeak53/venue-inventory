from __future__ import annotations

import os
import sqlite3
import tarfile
import threading
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

import pytest
from app.backups import (
    BackupError,
    create_backup,
    prune_backups,
    restore_backup,
    run_restore_drill,
    verify_backup,
)
from app.db import get_session
from app.images import image_directory
from app.models import InventoryItem
from app.times import naive_utc
from PIL import Image
from sqlalchemy import text
from tests.conftest import csrf_token, sign_in

GIT_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
BACKUP_TIME = datetime(2026, 8, 23, 3, 15, tzinfo=UTC)


def test_backup_contains_only_referenced_images_and_verifies(
    app, app_config, tmp_path: Path
) -> None:
    referenced = _seed_image_item(app, app_config.data_dir)
    orphan = image_directory(app_config.data_dir) / "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.webp"
    orphan.write_bytes(b"orphan-bytes")
    backup_root = tmp_path / "backups"
    archive = create_backup(
        data_dir=app_config.data_dir,
        backup_root=backup_root,
        git_sha=GIT_SHA,
        now=BACKUP_TIME,
    )
    assert archive.name == "venue-inventory-20260823T031500Z.tar.gz"
    manifest = verify_backup(archive)
    assert manifest.git_sha == GIT_SHA
    assert manifest.schema_revision == "0004_booking_selections"
    assert [entry.filename for entry in manifest.images] == [referenced]
    with tarfile.open(archive, "r:gz") as handle:
        names = {member.name for member in handle.getmembers()}
    assert names == {
        "database/venue-inventory.sqlite3",
        f"images/{referenced}",
        "manifest.json",
        "checksums.sha256",
    }


def test_backup_refuses_missing_referenced_image(app, app_config, tmp_path: Path) -> None:
    filename = _seed_image_item(app, app_config.data_dir)
    (image_directory(app_config.data_dir) / filename).unlink()
    with pytest.raises(BackupError, match="Referenced image"):
        create_backup(
            data_dir=app_config.data_dir,
            backup_root=tmp_path / "backups",
            git_sha=GIT_SHA,
            now=BACKUP_TIME,
        )
    assert list((tmp_path / "backups").glob("*.tar.gz")) == []


def test_backup_and_restore_support_an_empty_image_set(
    app, app_config, tmp_path: Path
) -> None:
    archive = create_backup(
        data_dir=app_config.data_dir,
        backup_root=tmp_path / "backups",
        git_sha=GIT_SHA,
        now=BACKUP_TIME,
    )
    manifest = verify_backup(archive)
    assert manifest.images == ()
    target = tmp_path / "isolated-empty-images"
    run_restore_drill(archive=archive, target_data_dir=target)
    assert (target / "images").is_dir()
    assert list((target / "images").iterdir()) == []


def test_backup_refuses_invalid_schema_metadata(app, app_config, tmp_path: Path) -> None:
    with app.app_context():
        session = get_session()
        session.execute(text("DELETE FROM alembic_version"))
        session.commit()
    with pytest.raises(BackupError, match="schema revision metadata"):
        create_backup(
            data_dir=app_config.data_dir,
            backup_root=tmp_path / "backups",
            git_sha=GIT_SHA,
            now=BACKUP_TIME,
        )


@pytest.mark.parametrize("corruption", ["manifest", "database", "image", "missing_image"])
def test_restore_rejects_corrupt_archives_before_changing_live_data(
    app, app_config, tmp_path: Path, corruption: str
) -> None:
    filename = _seed_image_item(app, app_config.data_dir)
    archive = create_backup(
        data_dir=app_config.data_dir,
        backup_root=tmp_path / "backups",
        git_sha=GIT_SHA,
        now=BACKUP_TIME,
    )
    corrupt = tmp_path / f"corrupt-{corruption}.tar.gz"
    _rewrite_archive(archive, corrupt, corruption)
    original_database = app_config.database_path.read_bytes()
    original_image = (image_directory(app_config.data_dir) / filename).read_bytes()
    with pytest.raises(BackupError):
        restore_backup(archive=corrupt, data_dir=app_config.data_dir)
    assert app_config.database_path.read_bytes() == original_database
    assert (image_directory(app_config.data_dir) / filename).read_bytes() == original_image
    assert list(app_config.data_dir.glob(".restore-prior-*")) == []


def test_restore_preserves_prior_data_and_isolated_drill_reproduces_bytes(
    app, app_config, tmp_path: Path
) -> None:
    filename = _seed_image_item(app, app_config.data_dir)
    original_image = (image_directory(app_config.data_dir) / filename).read_bytes()
    archive = create_backup(
        data_dir=app_config.data_dir,
        backup_root=tmp_path / "backups",
        git_sha=GIT_SHA,
        now=BACKUP_TIME,
    )
    expected = verify_backup(archive)
    with app.app_context():
        now = naive_utc(datetime(2026, 8, 24, tzinfo=UTC))
        session = get_session()
        session.add(
            InventoryItem(
                name="Changed after backup",
                description=None,
                stock_quantity=2,
                image_filename=None,
                is_visible=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    (image_directory(app_config.data_dir) / filename).write_bytes(b"changed-image")
    prior = restore_backup(archive=archive, data_dir=app_config.data_dir)
    assert prior.is_dir()
    connection = sqlite3.connect(prior / "venue-inventory.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM inventory_items").fetchone() == (
            2,
        )
    finally:
        connection.close()
    live = sqlite3.connect(app_config.database_path)
    try:
        assert live.execute("SELECT COUNT(*) FROM inventory_items").fetchone() == (1,)
    finally:
        live.close()
    assert app_config.database_path.stat().st_size == expected.database_bytes
    assert (image_directory(app_config.data_dir) / filename).read_bytes() == original_image
    assert (prior / "images" / filename).read_bytes() == b"changed-image"
    target = tmp_path / "isolated-data"
    result = run_restore_drill(archive=archive, target_data_dir=target)
    assert result == target
    isolated = sqlite3.connect(target / "venue-inventory.sqlite3")
    try:
        assert isolated.execute("SELECT COUNT(*) FROM inventory_items").fetchone() == (
            1,
        )
    finally:
        isolated.close()
    assert (image_directory(target) / filename).read_bytes() == original_image
    with pytest.raises(BackupError, match="empty"):
        run_restore_drill(archive=archive, target_data_dir=target)


def test_prune_only_removes_recognized_old_archives(tmp_path: Path) -> None:
    root = tmp_path / "dedicated-backups"
    root.mkdir()
    old = root / "venue-inventory-20260801T031500Z.tar.gz"
    recent = root / "venue-inventory-20260822T031500Z.tar.gz"
    unrelated = root / "other-20260801T031500Z.tar.gz"
    for path in (old, recent, unrelated):
        path.write_bytes(b"archive")
    os.utime(old, (BACKUP_TIME.timestamp() - 1_296_000, BACKUP_TIME.timestamp() - 1_296_000))
    os.utime(
        recent,
        (BACKUP_TIME.timestamp() - 1_123_200, BACKUP_TIME.timestamp() - 1_123_200),
    )
    os.utime(
        unrelated,
        (BACKUP_TIME.timestamp() - 2_592_000, BACKUP_TIME.timestamp() - 2_592_000),
    )
    cutoff_now = BACKUP_TIME
    prune_backups(backup_root=root, retention_days=14, now=cutoff_now)
    assert not old.exists()
    assert recent.exists()
    assert unrelated.exists()


def test_backup_lock_blocks_image_reference_mutation_until_snapshot_finishes(
    app, app_config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_image_item(app, app_config.data_dir)
    snapshot_started = threading.Event()
    mutation_started = threading.Event()
    snapshot_may_finish = threading.Event()
    observed_normalization = threading.Event()
    errors: list[BaseException] = []
    import app.backups as backups
    import app.views.admin as admin

    original_backup = backups._sqlite_online_backup
    original_normalize = admin.normalize_upload

    def pause_snapshot(source: Path, destination: Path) -> None:
        snapshot_started.set()
        mutation_started.wait(timeout=5)
        snapshot_may_finish.wait(timeout=5)
        original_backup(source, destination)

    def observe_normalization(upload, data_dir: Path) -> str:
        observed_normalization.set()
        return original_normalize(upload, data_dir)

    monkeypatch.setattr(backups, "_sqlite_online_backup", pause_snapshot)
    monkeypatch.setattr(admin, "normalize_upload", observe_normalization)

    def make_backup() -> None:
        try:
            create_backup(
                data_dir=app_config.data_dir,
                backup_root=tmp_path / "backups",
                git_sha=GIT_SHA,
                now=BACKUP_TIME,
            )
        except BaseException as exc:
            errors.append(exc)

    def mutate_image() -> None:
        try:
            client = app.test_client()
            response = sign_in(client)
            assert response.status_code == 302
            form_page = client.get("/admin/items/new")
            mutation_started.set()
            form = {
                "csrf_token": csrf_token(form_page),
                "name": "Concurrent image",
                "stock_quantity": "1",
                "is_visible": "1",
                "image": (_webp_upload(), "concurrent.webp", "image/webp"),
            }
            client.post("/admin/items", data=form, content_type="multipart/form-data")
        except BaseException as exc:
            errors.append(exc)

    backup_thread = threading.Thread(target=make_backup)
    mutation_thread = threading.Thread(target=mutate_image)
    backup_thread.start()
    assert snapshot_started.wait(timeout=5)
    mutation_thread.start()
    assert mutation_started.wait(timeout=5)
    assert not observed_normalization.wait(timeout=0.2)
    snapshot_may_finish.set()
    backup_thread.join(timeout=5)
    mutation_thread.join(timeout=5)
    assert errors == []
    assert observed_normalization.is_set()


def _seed_image_item(app, data_dir: Path, filename: str | None = None) -> str:
    if filename is None:
        filename = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp"
    directory = image_directory(data_dir)
    directory.mkdir(exist_ok=True)
    (directory / filename).write_bytes(b"referenced-image-bytes")
    with app.app_context():
        now = naive_utc(datetime(2026, 8, 23, tzinfo=UTC))
        session = get_session()
        session.add(
            InventoryItem(
                name="Backup chair",
                description=None,
                stock_quantity=1,
                image_filename=filename,
                is_visible=True,
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    return filename


def _rewrite_archive(source: Path, destination: Path, corruption: str) -> None:
    target_name = {
        "manifest": "manifest.json",
        "database": "database/venue-inventory.sqlite3",
        "image": "images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp",
        "missing_image": "images/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.webp",
    }[corruption]
    with tarfile.open(source, "r:gz") as incoming, tarfile.open(destination, "w:gz") as outgoing:
        for member in incoming.getmembers():
            payload_file = incoming.extractfile(member)
            assert payload_file is not None
            payload = payload_file.read()
            if corruption == "missing_image" and member.name == target_name:
                continue
            if member.name == target_name and corruption != "missing_image":
                payload = payload + b"x"
                member.size = len(payload)
            outgoing.addfile(member, BytesIO(payload))


def _webp_upload() -> BytesIO:
    output = BytesIO()
    Image.new("RGB", (2, 2), (40, 50, 60)).save(output, format="WEBP")
    output.seek(0)
    return output
