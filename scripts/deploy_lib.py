"""Pure deployment decisions used by VPS scripts.

This module is imported by tests and invoked as a small CLI from the
deploy scripts. It must not print secrets.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

REQUIRED_CHECK_NAME = "verify"
INVENTORY_HOSTNAME = "inventory.needleminder.app"
NEEDLEMINDER_HOSTNAME = "needleminder.app"
INVENTORY_UPSTREAM = "venue-inventory:8080"
PORKBUN_API_BASE = "https://api.porkbun.com/api/json/v3"
GITHUB_API_BASE = "https://api.github.com"
PUBLIC_IPV4 = "5.78.222.116"
CADDY_NETWORK_NAME = "needle-minder_default"
DEFAULT_REPO = "jmcpeak53/venue-inventory"


class DeployError(RuntimeError):
    """Raised when a deployment gate refuses to proceed."""


def evaluate_required_checks(
    check_runs: Sequence[Mapping[str, Any]],
    required_names: Sequence[str] | None = None,
) -> None:
    required = list(required_names or [REQUIRED_CHECK_NAME])
    by_name: dict[str, Mapping[str, Any]] = {}
    for run in check_runs:
        name = str(run.get("name") or "")
        if not name or name in by_name:
            continue
        by_name[name] = run

    missing = [name for name in required if name not in by_name]
    if missing:
        raise DeployError(
            "Deployment stopped: required CI check(s) missing for the target "
            f"commit: {', '.join(missing)}."
        )

    for name in required:
        run = by_name[name]
        status = str(run.get("status") or "")
        conclusion = str(run.get("conclusion") or "")
        if status != "completed":
            raise DeployError(
                f"Deployment stopped: required CI check {name!r} is {status}, "
                "not completed."
            )
        if conclusion != "success":
            raise DeployError(
                f"Deployment stopped: required CI check {name!r} conclusion is "
                f"{conclusion!r}, not success."
            )


def evaluate_checkout(
    *,
    dirty: bool,
    is_ancestor: bool,
    current_sha: str,
    target_sha: str,
) -> None:
    if dirty:
        raise DeployError(
            "Deployment stopped: the VPS checkout contains uncommitted changes."
        )
    if not current_sha or not target_sha:
        raise DeployError("Deployment stopped: Git SHA could not be determined.")
    if current_sha == target_sha:
        return
    if not is_ancestor:
        raise DeployError(
            "Deployment stopped: the VPS checkout is not a fast-forward to the "
            "target commit."
        )


def site_addresses(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "{" not in stripped:
        return []
    left = stripped.split("{", 1)[0]
    return [part.strip() for part in left.split(",") if part.strip()]


def caddyfile_has_site(caddyfile: str, hostname: str) -> bool:
    return any(hostname in site_addresses(line) for line in caddyfile.splitlines())


def render_inventory_caddy_block(
    hostname: str = INVENTORY_HOSTNAME,
    upstream: str = INVENTORY_UPSTREAM,
) -> str:
    return (
        f"{hostname} {{\n"
        f"    encode gzip\n"
        f"    reverse_proxy {upstream}\n"
        f"}}\n"
    )


def ensure_inventory_caddy_block(
    caddyfile: str,
    hostname: str = INVENTORY_HOSTNAME,
    upstream: str = INVENTORY_UPSTREAM,
) -> tuple[str, bool]:
    if caddyfile_has_site(caddyfile, hostname):
        normalized = caddyfile if caddyfile.endswith("\n") else caddyfile + "\n"
        return normalized, False
    base = caddyfile.rstrip() + "\n\n"
    return base + render_inventory_caddy_block(hostname, upstream), True


def record_name_matches(record_name: str, subdomain: str, domain: str) -> bool:
    name = record_name.rstrip(".").lower()
    subdomain = subdomain.lower()
    domain = domain.lower()
    candidates = {subdomain, f"{subdomain}.{domain}"}
    if subdomain == "":
        candidates.add(domain)
    return name in candidates


def matching_dns_records(
    records: Sequence[Mapping[str, Any]],
    *,
    subdomain: str,
    domain: str,
    rtype: str,
) -> list[Mapping[str, Any]]:
    wanted_type = rtype.upper()
    matched: list[Mapping[str, Any]] = []
    for record in records:
        if str(record.get("type") or "").upper() != wanted_type:
            continue
        if record_name_matches(str(record.get("name") or ""), subdomain, domain):
            matched.append(record)
    return matched


def dns_ensure_action(
    records: Sequence[Mapping[str, Any]],
    *,
    subdomain: str,
    domain: str,
    rtype: str,
    content: str,
) -> str:
    matches = matching_dns_records(
        records, subdomain=subdomain, domain=domain, rtype=rtype
    )
    if not matches:
        return "create"
    contents = {str(item.get("content") or "").strip() for item in matches}
    if contents == {content}:
        return "exists"
    return "update"


def can_close_public_port(
    *,
    inventory_https_ok: bool,
    needleminder_https_ok: bool,
    caddy_can_reach_app: bool,
) -> bool:
    return inventory_https_ok and needleminder_https_ok and caddy_can_reach_app


def parse_backup_archive_path(output: str) -> str | None:
    pattern = re.compile(
        r"(?:^|\s)(/[^\s]*venue-inventory-\d{8}T\d{6}Z(?:-\d+)?\.tar\.gz)(?:\s|$)",
        re.MULTILINE,
    )
    matches = pattern.findall(output)
    if not matches:
        return None
    return matches[-1]


@dataclass(frozen=True)
class RollbackPlan:
    reset_sha: str
    restore_archive: str | None
    compose_prod: bool


def plan_rollback(
    *,
    prior_sha: str,
    backup_archive: str | None,
    prior_has_prod_overlay: bool,
) -> RollbackPlan:
    if not prior_sha:
        raise DeployError("Rollback is not possible without a prior healthy SHA.")
    return RollbackPlan(
        reset_sha=prior_sha,
        restore_archive=backup_archive or None,
        compose_prod=prior_has_prod_overlay,
    )


def should_skip_stage(current: str, fail_after: str | None) -> bool:
    return bool(fail_after) and current == fail_after


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
    timeout: int = 30,
) -> tuple[Any, Mapping[str, str]]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return parsed, dict(response.headers)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DeployError(
            f"HTTP {exc.code} from {url.split('?')[0]}: {detail[:300]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise DeployError(f"Network error contacting {url.split('?')[0]}.") from exc


def github_token_from_env(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return (source.get("GITHUB_TOKEN") or source.get("GH_TOKEN") or "").strip()


def fetch_check_runs(
    *,
    repo: str,
    sha: str,
    token: str,
    opener: Any = None,
) -> list[dict[str, Any]]:
    if not token:
        raise DeployError(
            "Deployment stopped: GitHub token missing. Set GH_TOKEN or "
            "GITHUB_TOKEN, or authenticate gh."
        )
    if opener is not None:
        return opener(repo=repo, sha=sha, token=token)

    runs: list[dict[str, Any]] = []
    url: str | None = (
        f"{GITHUB_API_BASE}/repos/{repo}/commits/{sha}/check-runs?per_page=100"
    )
    while url:
        payload, headers = _json_request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": "venue-inventory-deploy",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        runs.extend(payload.get("check_runs") or [])
        url = _next_link(headers.get("Link") or headers.get("link") or "")
    return runs


def _next_link(link_header: str) -> str | None:
    for part in link_header.split(","):
        if 'rel="next"' in part:
            start = part.find("<")
            end = part.find(">", start)
            if start >= 0 and end > start:
                return part[start + 1 : end]
    return None


def porkbun_credentials(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    source = env if env is not None else os.environ
    key = (source.get("PORKBUN_API_KEY") or "").strip()
    secret = (source.get("PORKBUN_SECRET_API_KEY") or "").strip()
    if not key or not secret:
        raise DeployError(
            "Deployment stopped: Porkbun API credentials are missing. Set "
            "PORKBUN_API_KEY and PORKBUN_SECRET_API_KEY for the coding agent; "
            "the operator should not create DNS records by hand."
        )
    return key, secret


def ensure_porkbun_a_record(
    *,
    domain: str,
    subdomain: str,
    content: str,
    apikey: str,
    secretapikey: str,
    request_json: Any = None,
) -> str:
    requester = request_json or _porkbun_post
    auth = {"apikey": apikey, "secretapikey": secretapikey}
    retrieve = requester(
        f"{PORKBUN_API_BASE}/dns/retrieveByNameType/{domain}/A/{subdomain}",
        auth,
    )
    if str(retrieve.get("status") or "").upper() != "SUCCESS":
        raise DeployError(
            "Deployment stopped: Porkbun DNS lookup failed for "
            f"{subdomain}.{domain}."
        )
    action = dns_ensure_action(
        retrieve.get("records") or [],
        subdomain=subdomain,
        domain=domain,
        rtype="A",
        content=content,
    )
    if action == "exists":
        return "exists"
    if action == "create":
        created = requester(
            f"{PORKBUN_API_BASE}/dns/create/{domain}",
            {
                **auth,
                "name": subdomain,
                "type": "A",
                "content": content,
                "ttl": 600,
            },
        )
        if str(created.get("status") or "").upper() != "SUCCESS":
            raise DeployError(
                f"Deployment stopped: Porkbun could not create {subdomain}.{domain}."
            )
        return "created"
    edited = requester(
        f"{PORKBUN_API_BASE}/dns/editByNameType/{domain}/A/{subdomain}",
        {**auth, "content": content, "ttl": 600},
    )
    if str(edited.get("status") or "").upper() != "SUCCESS":
        raise DeployError(
            f"Deployment stopped: Porkbun could not update {subdomain}.{domain}."
        )
    return "updated"


def _porkbun_post(url: str, body: Mapping[str, Any]) -> dict[str, Any]:
    payload, _headers = _json_request(url, method="POST", body=body)
    if not isinstance(payload, dict):
        raise DeployError("Porkbun returned a non-object response.")
    return payload


def _load_optional_env_file(path: str) -> None:
    if not path or not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def _cli_check_ci(args: argparse.Namespace) -> int:
    token = args.token or github_token_from_env()
    runs = fetch_check_runs(repo=args.repo, sha=args.sha, token=token)
    evaluate_required_checks(runs, args.required.split(",") if args.required else None)
    print(f"CI checks passed for {args.sha[:12]}.")
    return 0


def _cli_ensure_dns(args: argparse.Namespace) -> int:
    _load_optional_env_file(args.env_file)
    key, secret = porkbun_credentials()
    action = ensure_porkbun_a_record(
        domain=args.domain,
        subdomain=args.subdomain,
        content=args.content,
        apikey=key,
        secretapikey=secret,
    )
    print(f"DNS {args.subdomain}.{args.domain} A {args.content}: {action}")
    return 0


def _cli_patch_caddyfile(args: argparse.Namespace) -> int:
    current = sys.stdin.read() if args.input == "-" else open(args.input, encoding="utf-8").read()
    patched, changed = ensure_inventory_caddy_block(
        current, hostname=args.hostname, upstream=args.upstream
    )
    if args.output == "-":
        sys.stdout.write(patched)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(patched)
    print("changed" if changed else "unchanged", file=sys.stderr)
    return 0


def _cli_can_close_port(args: argparse.Namespace) -> int:
    allowed = can_close_public_port(
        inventory_https_ok=args.inventory_https_ok,
        needleminder_https_ok=args.needleminder_https_ok,
        caddy_can_reach_app=args.caddy_can_reach_app,
    )
    print("yes" if allowed else "no")
    return 0 if allowed else 2


def _cli_parse_backup(args: argparse.Namespace) -> int:
    parsed = parse_backup_archive_path(sys.stdin.read())
    if not parsed:
        return 1
    if args.backup_dir and parsed.startswith("/backups/"):
        parsed = args.backup_dir.rstrip("/") + "/" + parsed.rsplit("/", 1)[-1]
    print(parsed)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Venue Inventory deploy helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check-ci", help="Refuse a SHA without successful CI")
    check.add_argument("--repo", default=DEFAULT_REPO)
    check.add_argument("--sha", required=True)
    check.add_argument("--required", default=REQUIRED_CHECK_NAME)
    check.add_argument("--token", default="")
    check.set_defaults(func=_cli_check_ci)

    dns = sub.add_parser("ensure-dns", help="Idempotently create the inventory A record")
    dns.add_argument("--domain", default=NEEDLEMINDER_HOSTNAME)
    dns.add_argument("--subdomain", default="inventory")
    dns.add_argument("--content", default=PUBLIC_IPV4)
    dns.add_argument(
        "--env-file",
        default=os.path.expanduser("~/.config/venue-inventory/porkbun.env"),
    )
    dns.set_defaults(func=_cli_ensure_dns)

    caddy = sub.add_parser("patch-caddyfile", help="Add the inventory reverse-proxy block")
    caddy.add_argument("--input", required=True)
    caddy.add_argument("--output", default="-")
    caddy.add_argument("--hostname", default=INVENTORY_HOSTNAME)
    caddy.add_argument("--upstream", default=INVENTORY_UPSTREAM)
    caddy.set_defaults(func=_cli_patch_caddyfile)

    close_port = sub.add_parser("can-close-port")
    close_port.add_argument("--inventory-https-ok", action="store_true")
    close_port.add_argument("--needleminder-https-ok", action="store_true")
    close_port.add_argument("--caddy-can-reach-app", action="store_true")
    close_port.set_defaults(func=_cli_can_close_port)

    backup = sub.add_parser("parse-backup-path", help="Extract the archive path from backup output")
    backup.add_argument("--backup-dir", default="")
    backup.set_defaults(func=_cli_parse_backup)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except DeployError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
