from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from app.hash_password import generate_admin_password, main, render_prod_env

ROOT = Path(__file__).resolve().parent.parent
NEEDLEMINDER_CADDYFILE = """needleminder.app, www.needleminder.app {
    root * /usr/share/caddy/needle-minder/website/Design
    file_server
}
"""


def load_deploy_lib():
    path = ROOT / "scripts" / "deploy_lib.py"
    spec = importlib.util.spec_from_file_location("deploy_lib", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy_lib = load_deploy_lib()


def test_required_checks_accept_successful_verify() -> None:
    deploy_lib.evaluate_required_checks(
        [
            {"name": "verify", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "in_progress", "conclusion": None},
        ]
    )


def test_required_checks_reject_missing_pending_and_failed() -> None:
    with pytest.raises(deploy_lib.DeployError, match="missing"):
        deploy_lib.evaluate_required_checks([])
    with pytest.raises(deploy_lib.DeployError, match="in_progress"):
        deploy_lib.evaluate_required_checks(
            [{"name": "verify", "status": "in_progress", "conclusion": None}]
        )
    with pytest.raises(deploy_lib.DeployError, match="failure"):
        deploy_lib.evaluate_required_checks(
            [{"name": "verify", "status": "completed", "conclusion": "failure"}]
        )


def test_checkout_gates_dirty_and_divergent_history() -> None:
    deploy_lib.evaluate_checkout(
        dirty=False,
        is_ancestor=True,
        current_sha="aaa",
        target_sha="bbb",
    )
    deploy_lib.evaluate_checkout(
        dirty=False,
        is_ancestor=False,
        current_sha="same",
        target_sha="same",
    )
    with pytest.raises(deploy_lib.DeployError, match="uncommitted"):
        deploy_lib.evaluate_checkout(
            dirty=True, is_ancestor=True, current_sha="aaa", target_sha="bbb"
        )
    with pytest.raises(deploy_lib.DeployError, match="fast-forward"):
        deploy_lib.evaluate_checkout(
            dirty=False, is_ancestor=False, current_sha="aaa", target_sha="bbb"
        )


def test_caddyfile_inventory_block_is_idempotent_and_isolated() -> None:
    patched, changed = deploy_lib.ensure_inventory_caddy_block(NEEDLEMINDER_CADDYFILE)
    assert changed is True
    assert "needleminder.app, www.needleminder.app" in patched
    assert "root * /usr/share/caddy/needle-minder/website/Design" in patched
    assert "inventory.needleminder.app {" in patched
    assert "reverse_proxy venue-inventory:8080" in patched
    again, changed_again = deploy_lib.ensure_inventory_caddy_block(patched)
    assert changed_again is False
    assert again == patched


def test_dns_ensure_action_create_exists_and_update() -> None:
    records = [
        {
            "name": "inventory.needleminder.app",
            "type": "A",
            "content": "5.78.222.116",
        }
    ]
    kwargs = {
        "subdomain": "inventory",
        "domain": "needleminder.app",
        "rtype": "A",
        "content": "5.78.222.116",
    }
    assert deploy_lib.dns_ensure_action([], **kwargs) == "create"
    assert deploy_lib.dns_ensure_action(records, **kwargs) == "exists"
    records[0]["content"] = "1.2.3.4"
    assert deploy_lib.dns_ensure_action(records, **kwargs) == "update"


def test_ensure_porkbun_record_skips_identical_and_creates_missing() -> None:
    calls: list[str] = []

    def requester(url: str, body: dict) -> dict:
        calls.append(url)
        if "retrieveByNameType" in url:
            return {"status": "SUCCESS", "records": []}
        return {"status": "SUCCESS", "id": "123"}

    action = deploy_lib.ensure_porkbun_a_record(
        domain="needleminder.app",
        subdomain="inventory",
        content="5.78.222.116",
        apikey="pk",
        secretapikey="sk",
        request_json=requester,
    )
    assert action == "created"
    assert any("dns/create" in url for url in calls)

    def existing(url: str, body: dict) -> dict:
        return {
            "status": "SUCCESS",
            "records": [
                {
                    "name": "inventory",
                    "type": "A",
                    "content": "5.78.222.116",
                }
            ],
        }

    assert (
        deploy_lib.ensure_porkbun_a_record(
            domain="needleminder.app",
            subdomain="inventory",
            content="5.78.222.116",
            apikey="pk",
            secretapikey="sk",
            request_json=existing,
        )
        == "exists"
    )


def test_public_port_closes_only_after_both_sites_and_proxy_path_work() -> None:
    assert (
        deploy_lib.can_close_public_port(
            inventory_https_ok=True,
            needleminder_https_ok=True,
            caddy_can_reach_app=True,
        )
        is True
    )
    assert (
        deploy_lib.can_close_public_port(
            inventory_https_ok=True,
            needleminder_https_ok=True,
            caddy_can_reach_app=False,
        )
        is False
    )


def test_parse_backup_archive_path_maps_container_path() -> None:
    output = "Creating verified backup\n/backups/venue-inventory-20260823T181500Z.tar.gz\n"
    parsed = deploy_lib.parse_backup_archive_path(output)
    assert parsed == "/backups/venue-inventory-20260823T181500Z.tar.gz"


def test_parse_backup_cli_rewrites_container_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        deploy_lib.sys,
        "stdin",
        io.StringIO(" /backups/venue-inventory-20260823T181500Z.tar.gz \n"),
    )
    assert (
        deploy_lib.main(
            ["parse-backup-path", "--backup-dir", "/opt/venue-inventory/backups"]
        )
        == 0
    )
    assert (
        capsys.readouterr().out.strip()
        == "/opt/venue-inventory/backups/venue-inventory-20260823T181500Z.tar.gz"
    )


def test_rollback_plan_requires_prior_sha_and_keeps_backup_pairing() -> None:
    plan = deploy_lib.plan_rollback(
        prior_sha="abc123",
        backup_archive="/opt/venue-inventory/backups/venue-inventory-20260823T181500Z.tar.gz",
        prior_has_prod_overlay=False,
    )
    assert plan.reset_sha == "abc123"
    assert plan.restore_archive.endswith(".tar.gz")
    with pytest.raises(deploy_lib.DeployError, match="prior healthy SHA"):
        deploy_lib.plan_rollback(
            prior_sha="", backup_archive=None, prior_has_prod_overlay=False
        )


def test_induced_failure_stage_matches_exact_name() -> None:
    assert deploy_lib.should_skip_stage("readiness", "readiness") is True
    assert deploy_lib.should_skip_stage("readiness", "smoke") is False
    assert deploy_lib.should_skip_stage("readiness", None) is False


def test_prod_overlay_joins_external_caddy_network_without_local_container_name() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    prod = (ROOT / "compose.prod.yaml").read_text(encoding="utf-8")
    assert "container_name:" not in compose
    assert "needle-minder_default" in prod
    assert "external: true" in prod
    assert "VENUE_INVENTORY_TRUST_PROXY: \"true\"" in prod
    assert "/run/secrets/admin-password.hash" in prod


def test_deploy_script_gates_ci_and_dns_before_ssh() -> None:
    local = (ROOT / "scripts/deploy-vps.sh").read_text(encoding="utf-8")
    remote = (ROOT / "scripts/deploy-remote.sh").read_text(encoding="utf-8")
    assert local.index("check-ci") < local.index("bash -s --")
    assert local.index("ensure-dns") < local.index("bash -s --")
    assert "PRIOR_HEALTHY_SHA" in local
    assert "git merge --ff-only" in local
    assert remote.index("pre-deployment backup") < remote.index(
        "Replacing the running container"
    )
    assert 'restore-vps.sh "$backup_archive"' in remote
    assert remote.index('restore-vps.sh "$backup_archive"') < remote.index(
        'git reset --hard "$prior_sha"'
    )
    assert remote.index("./scripts/ensure-caddy-inventory.sh") < remote.index(
        "if ! close_public_8080"
    )
    assert 'cp -a "$caddy_backup"' in remote
    assert remote.index("Restoring Caddyfile") < remote.index(
        'git reset --hard "$prior_sha"'
    )
    assert "caddy validate" in (ROOT / "scripts/ensure-caddy-inventory.sh").read_text(
        encoding="utf-8"
    )


def test_bootstrap_and_rollback_drill_are_idempotent_and_isolated() -> None:
    bootstrap = (ROOT / "scripts/bootstrap-prod-secrets.sh").read_text(encoding="utf-8")
    drill = (ROOT / "scripts/rollback-drill-vps.sh").read_text(encoding="utf-8")
    caddy = (ROOT / "scripts/ensure-caddy-inventory.sh").read_text(encoding="utf-8")
    assert "leaving them unchanged" in bootstrap
    assert "chmod 600" in bootstrap or "0o600" in bootstrap
    assert "chown 1000:1000" in bootstrap
    assert "VENUE_INVENTORY_SKIP_INGRESS=1" in drill
    assert "VENUE_INVENTORY_DEPLOY_FAIL_AFTER=readiness" in drill
    assert "cp -a \"$caddyfile\" \"$backup\"" in caddy
    assert "Needleminder is not healthy" in caddy


def test_hash_password_generate_and_bootstrap_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = generate_admin_password()
    assert len(password) == 20
    assert "I" not in password and "l" not in password and "0" not in password
    main(["--generate"])
    generated = capsys.readouterr().out
    assert "$argon2id$" in generated
    assert "Generated administrator password" in generated
    main(["--bootstrap-json"])
    payload = json.loads(capsys.readouterr().out)
    assert "password" in payload and payload["password"]
    assert payload["hash"].startswith("$argon2id$")
    assert "VENUE_INVENTORY_SECRET_KEY=" in payload["env"]
    assert payload["password"] not in payload["env"]
    env = render_prod_env(
        secret_key="s" * 32,
        access_code_hmac_secret="h" * 32,
        bind_address="127.0.0.1",
    )
    assert "VENUE_INVENTORY_TRUST_PROXY=true" in env
    assert "VENUE_INVENTORY_BIND_ADDRESS=127.0.0.1" in env
