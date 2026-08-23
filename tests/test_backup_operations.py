from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_systemd_backup_units_render_for_a_vps_checkout_without_live_systemd() -> None:
    deploy_directory = "/opt/venue-inventory"
    service = (ROOT / "systemd/venue-inventory-backup.service").read_text(encoding="utf-8")
    timer = (ROOT / "systemd/venue-inventory-backup.timer").read_text(encoding="utf-8")
    rendered = service.replace("@DEPLOY_DIRECTORY@", deploy_directory)
    assert "WorkingDirectory=/opt/venue-inventory" in rendered
    assert "ExecStart=/opt/venue-inventory/scripts/run-backup-vps.sh" in rendered
    assert "OnCalendar=*-*-* 03:15:00" in timer
    assert "Persistent=true" in timer


def test_timer_installer_only_replaces_changed_units() -> None:
    installer = (ROOT / "scripts/install-backup-timer-vps.sh").read_text(encoding="utf-8")
    assert "sudo cmp -s" in installer
    assert "sudo install -m 0644" in installer
    assert "sudo systemctl daemon-reload" in installer
    assert "sudo systemctl enable --now venue-inventory-backup.timer" in installer


def test_compose_mounts_a_dedicated_backup_directory() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "${VENUE_INVENTORY_BACKUP_DIR:-./backups}:/backups" in compose


def test_backup_script_chowns_backup_dir_for_container_user() -> None:
    backup = (ROOT / "scripts/run-backup-vps.sh").read_text(encoding="utf-8")
    assert "chown 1000:1000" in backup
    assert backup.index("chown 1000:1000") < backup.index("backup --data-dir")
    # deploy-vps.sh is omitted from the verify image (.dockerignore). When the
    # host file is present, it must chown before the pre-deployment backup.
    deploy_path = ROOT / "scripts/deploy-vps.sh"
    if deploy_path.is_file():
        deploy = deploy_path.read_text(encoding="utf-8")
        assert "chown 1000:1000" in deploy
        assert deploy.index("chown 1000:1000") < deploy.index("run-backup-vps.sh")


def test_restore_drill_migrates_isolated_data_before_readiness_smoke() -> None:
    drill = (ROOT / "scripts/restore-drill-vps.sh").read_text(encoding="utf-8")
    assert "upgrade_to_head" in drill
    assert drill.index("upgrade_to_head") < drill.index('("/readyz", 200)')


def test_restore_drill_chowns_isolated_data_for_container_user() -> None:
    drill = (ROOT / "scripts/restore-drill-vps.sh").read_text(encoding="utf-8")
    assert 'chown 1000:1000 "$drill_root/data"' in drill
    assert drill.index('chown 1000:1000 "$drill_root/data"') < drill.index(
        "-m app.backups restore-drill"
    )
