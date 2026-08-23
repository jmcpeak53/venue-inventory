#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

secrets_dir="${VENUE_INVENTORY_SECRETS_DIR:-/etc/venue-inventory}"
secrets_file="${VENUE_INVENTORY_SECRETS_FILE:-$secrets_dir/prod.env}"
public_port="${VENUE_INVENTORY_PORT:-8080}"
bind_address="${VENUE_INVENTORY_BIND_ADDRESS:-0.0.0.0}"
fail_after="${VENUE_INVENTORY_DEPLOY_FAIL_AFTER:-}"
skip_ingress="${VENUE_INVENTORY_SKIP_INGRESS:-0}"
skip_firewall="${VENUE_INVENTORY_SKIP_FIREWALL:-0}"
inventory_url="${VENUE_INVENTORY_PUBLIC_URL:-https://inventory.needleminder.app}"
needleminder_url="${NEEDLEMINDER_URL:-https://needleminder.app/}"
caddy_container="${VENUE_INVENTORY_CADDY_CONTAINER:-caddy-needle-minder}"
caddy_network="${VENUE_INVENTORY_CADDY_NETWORK:-needle-minder_default}"

if ! [[ "$public_port" =~ ^[0-9]+$ ]] || ((public_port < 1 || public_port > 65535)); then
  echo "VENUE_INVENTORY_PORT must be a number from 1 through 65535." >&2
  exit 2
fi

prior_sha="${PRIOR_HEALTHY_SHA:-}"
if [[ -z "$prior_sha" ]]; then
  echo "PRIOR_HEALTHY_SHA is required so rollback can restore the previous revision." >&2
  exit 2
fi
backup_archive=""
caddy_backup=""
rolled_back=0

induce_failure() {
  local stage="$1"
  if [[ -n "$fail_after" && "$fail_after" == "$stage" ]]; then
    echo "Induced failure after ${stage}." >&2
    return 1
  fi
  return 0
}

use_prod_overlay() {
  [[ "$skip_ingress" != "1" && -f "$root/compose.prod.yaml" && -f "$secrets_file" ]]
}

compose() {
  if use_prod_overlay; then
    docker compose --env-file "$secrets_file" -f compose.yaml -f compose.prod.yaml "$@"
  else
    docker compose -f compose.yaml "$@"
  fi
}

internal_health() {
  local path="$1"
  curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:${public_port}${path}"
}

caddy_can_reach_app() {
  docker exec "$caddy_container" wget -q -O- --timeout=5 "http://venue-inventory:8080/healthz" | grep -qx "ok"
}

container_on_caddy_network() {
  docker inspect venue-inventory --format '{{json .NetworkSettings.Networks}}' 2>/dev/null \
    | grep -q "$caddy_network"
}

https_ok() {
  local url="$1"
  curl --fail --silent --show-error --max-time 30 -o /dev/null "$url"
}

needleminder_ok() {
  https_ok "$needleminder_url"
}

inventory_https_ok() {
  local body
  body="$(curl --fail --silent --show-error --max-time 30 "${inventory_url}/healthz" || true)"
  [[ "$body" == "ok" || "$body" == $'ok\n' ]]
}

http_redirects_to_https() {
  local code
  code="$(curl --silent --show-error --max-time 20 -o /dev/null -w '%{http_code}' "http://inventory.needleminder.app/healthz" || true)"
  [[ "$code" == "308" || "$code" == "301" || "$code" == "302" ]]
}

set_bind_address() {
  local value="$1"
  python3 - "$secrets_file" "$value" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
value = sys.argv[2]
text = path.read_text(encoding="utf-8")
lines = []
found = False
for line in text.splitlines():
    if line.startswith("VENUE_INVENTORY_BIND_ADDRESS="):
        lines.append(f"VENUE_INVENTORY_BIND_ADDRESS={value}")
        found = True
    else:
        lines.append(line)
if not found:
    lines.append(f"VENUE_INVENTORY_BIND_ADDRESS={value}")
path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
}

compose_up() {
  local sha
  sha="$(git rev-parse HEAD)"
  VENUE_INVENTORY_DEPLOYED_GIT_SHA="$sha" \
    VENUE_INVENTORY_PORT="$public_port" \
    VENUE_INVENTORY_BIND_ADDRESS="$bind_address" \
    compose up -d --build --remove-orphans --wait --wait-timeout 60
}

rollback() {
  local reason="$1"
  if [[ "$rolled_back" -eq 1 ]]; then
    echo "Rollback already attempted after: ${reason}" >&2
    return 1
  fi
  rolled_back=1
  echo "Deployment failed (${reason}); restoring prior healthy SHA ${prior_sha}." >&2
  if [[ -n "$caddy_backup" && -f "$caddy_backup" ]]; then
    echo "Restoring Caddyfile from ${caddy_backup}." >&2
    cp -a "$caddy_backup" "${VENUE_INVENTORY_CADDYFILE:-/apps/needle-minder/Caddyfile}"
    docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile >/dev/null || true
  fi
  if [[ -n "$backup_archive" && -x "$root/scripts/restore-vps.sh" && -f "$backup_archive" ]]; then
    echo "Restoring paired backup ${backup_archive} before returning to ${prior_sha}." >&2
    VENUE_INVENTORY_PORT="$public_port" ./scripts/restore-vps.sh "$backup_archive" || true
  fi
  git reset --hard "$prior_sha"
  if ! VENUE_INVENTORY_PORT="$public_port" compose_up; then
    echo "Rollback container start failed." >&2
    compose ps >&2 || true
    compose logs --tail=100 web >&2 || true
    return 1
  fi
  if ! internal_health /healthz >/dev/null; then
    echo "Rollback health check failed." >&2
    return 1
  fi
  echo "Rollback restored ${prior_sha} and verified health."
}

close_public_8080() {
  if ! command -v ufw >/dev/null; then
    echo "ufw is not installed; not changing firewall rules." >&2
    return 1
  fi
  if ! ufw status | grep -qE '8080/tcp'; then
    echo "Public TCP 8080 firewall rule is already absent."
    return 0
  fi
  ufw --force delete allow 8080/tcp >/dev/null || true
  ufw --force delete allow 8080/tcp >/dev/null || true
  if ufw status | grep -qE '8080/tcp'; then
    echo "Failed to remove the public TCP 8080 firewall rule." >&2
    return 1
  fi
  echo "Removed the public TCP 8080 firewall rule."
}

echo "Building the replacement image before backup and migration"
if ! docker compose -f compose.yaml build web; then
  echo "Deployment stopped: the replacement image could not be built." >&2
  exit 1
fi

if [[ "$skip_ingress" != "1" ]]; then
  ./scripts/bootstrap-prod-secrets.sh
fi

backup_dir="${VENUE_INVENTORY_BACKUP_DIR:-$root/backups}"
mkdir -p "$backup_dir"
chown 1000:1000 "$backup_dir"

set +e
docker compose -f compose.yaml run --rm --no-deps --entrypoint python web -c \
  'from pathlib import Path; raise SystemExit(0 if Path("/data/venue-inventory.sqlite3").is_file() else 3)'
inspect_status=$?
set -e
if [[ "$inspect_status" -eq 0 ]]; then
  echo "Creating a verified pre-deployment backup before migrations"
  backup_output=""
  if ! backup_output="$(VENUE_INVENTORY_BACKUP_USE_RUN=1 ./scripts/run-backup-vps.sh)"; then
    echo "Deployment stopped: pre-deployment backup failed; refusing to migrate." >&2
    printf '%s\n' "$backup_output" >&2
    exit 1
  fi
  printf '%s\n' "$backup_output"
  backup_archive="$(
    printf '%s\n' "$backup_output" | python3 "$root/scripts/deploy_lib.py" parse-backup-path --backup-dir "$backup_dir" || true
  )"
  if [[ -z "$backup_archive" ]]; then
    host_latest="$(ls -1t "$backup_dir"/venue-inventory-*.tar.gz 2>/dev/null | head -n 1 || true)"
    backup_archive="$host_latest"
  fi
  if [[ -z "$backup_archive" ]]; then
    echo "Deployment stopped: backup succeeded but no archive path was recorded." >&2
    exit 1
  fi
  echo "Pre-deployment backup archive: $backup_archive"
elif [[ "$inspect_status" -eq 3 ]]; then
  echo "No application database yet; skipping pre-deployment backup."
else
  echo "Deployment stopped: could not inspect the data volume before backup." >&2
  exit 1
fi

induce_failure backup || {
  rollback "induced backup failure"
  exit 1
}

echo "Replacing the running container and applying migrations"
if ! compose_up; then
  echo "Deployment did not become healthy within 60 seconds." >&2
  compose ps >&2 || true
  compose logs --tail=100 web >&2 || true
  rollback "container start or readiness"
  exit 1
fi
if ! internal_health /healthz >/dev/null || ! internal_health /readyz >/dev/null; then
  rollback "readiness probe"
  exit 1
fi
induce_failure readiness || {
  rollback "induced readiness failure"
  exit 1
}

if [[ "$skip_ingress" != "1" ]]; then
  if ! needleminder_ok; then
    rollback "Needleminder was unhealthy before ingress changes"
    exit 1
  fi
  if ! container_on_caddy_network; then
    rollback "application is not on the Caddy Docker network"
    exit 1
  fi
  if ! caddy_can_reach_app; then
    rollback "Caddy cannot reach venue-inventory on the shared network"
    exit 1
  fi
  caddy_output="$(./scripts/ensure-caddy-inventory.sh)" || {
    printf '%s\n' "$caddy_output" >&2
    rollback "Caddy inventory ingress"
    exit 1
  }
  printf '%s\n' "$caddy_output"
  caddy_backup="$(printf '%s\n' "$caddy_output" | awk -F= '/^caddy_backup=/ {print $2}' | tail -n 1)"

  smoke_ok=0
  for _attempt in $(seq 1 30); do
    if inventory_https_ok && http_redirects_to_https && needleminder_ok; then
      smoke_ok=1
      break
    fi
    sleep 10
  done
  if [[ "$smoke_ok" -ne 1 ]]; then
    rollback "public HTTPS smoke"
    exit 1
  fi
  induce_failure smoke || {
    rollback "induced smoke failure"
    exit 1
  }

  if [[ "$skip_firewall" != "1" ]]; then
    bind_address="127.0.0.1"
    export VENUE_INVENTORY_BIND_ADDRESS="$bind_address"
    set_bind_address "127.0.0.1"
    if ! compose_up; then
      rollback "rebinding the application off the public interface"
      exit 1
    fi
    close_args=()
    if inventory_https_ok; then close_args+=(--inventory-https-ok); fi
    if needleminder_ok; then close_args+=(--needleminder-https-ok); fi
    if caddy_can_reach_app; then close_args+=(--caddy-can-reach-app); fi
    if ! python3 "$root/scripts/deploy_lib.py" can-close-port "${close_args[@]}"; then
      rollback "public port close gate"
      exit 1
    fi
    if ! close_public_8080; then
      rollback "firewall close of TCP 8080"
      exit 1
    fi
    if ! inventory_https_ok || ! needleminder_ok; then
      rollback "health after closing public port 8080"
      exit 1
    fi
  fi

  if [[ -x "$root/scripts/install-backup-timer-vps.sh" ]]; then
    ./scripts/install-backup-timer-vps.sh
  fi
fi

echo "Deployment is healthy."
echo "deployed_sha=$(git rev-parse HEAD)"
echo "prior_sha=${prior_sha}"
if [[ -n "$backup_archive" ]]; then
  echo "backup_archive=${backup_archive}"
fi
echo "public_url=${inventory_url}"
compose ps
