#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /backups/venue-inventory-YYYYMMDDTHHMMSSZ.tar.gz" >&2
  exit 2
fi

archive_host="$1"
if [[ "$archive_host" != /* ]]; then
  echo "The backup archive path must be absolute." >&2
  exit 2
fi
if [[ ! -f "$archive_host" ]]; then
  echo "Backup archive does not exist: $archive_host" >&2
  exit 1
fi

backup_root="${VENUE_INVENTORY_BACKUP_DIR:-$root/backups}"
mkdir -p "$backup_root"
archive_name="$(basename "$archive_host")"
if [[ "$archive_host" != "$backup_root/$archive_name" ]]; then
  cp -f "$archive_host" "$backup_root/$archive_name"
fi
archive_container="/backups/${archive_name}"
public_port="${VENUE_INVENTORY_PORT:-8080}"
owner="${VENUE_INVENTORY_RESTORE_OWNER:-1000:1000}"

run_python() {
  COMPOSE_PROGRESS=quiet docker compose run --rm --no-deps --entrypoint python web "$@"
}

echo "Verifying $archive_container before changing live data"
run_python -m app.backups verify "$archive_container"

echo "Stopping application writes"
docker compose stop web

prior=""
restore_status=0
set +e
restore_output="$(run_python -m app.backups restore --archive "$archive_container" --data-dir /data --owner "$owner")"
restore_status=$?
set -e
prior="$(printf '%s\n' "$restore_output" | grep '\.restore-prior-' | tail -n 1 || true)"
if [[ "$restore_status" -ne 0 || -z "$prior" ]]; then
  echo "Restore failed before the service was restarted. Starting the previous container." >&2
  docker compose up -d --wait --wait-timeout 60 || true
  exit 1
fi

prior_name="$(basename "$prior")"
echo "Preserved prior live data at ${prior_name} until verification succeeds."

if ! docker compose up -d --wait --wait-timeout 60; then
  echo "Restored application did not become healthy. Rolling back prior data." >&2
  docker compose stop web || true
  run_python -m app.backups rollback --data-dir /data --prior "$prior_name" --owner "$owner" || true
  docker compose up -d --wait --wait-timeout 60 || true
  exit 1
fi

if ! curl --fail --silent --show-error "http://127.0.0.1:${public_port}/readyz" >/dev/null; then
  echo "Readiness check failed after restore. Rolling back prior data." >&2
  docker compose stop web || true
  run_python -m app.backups rollback --data-dir /data --prior "$prior_name" --owner "$owner" || true
  docker compose up -d --wait --wait-timeout 60 || true
  exit 1
fi

echo "Restore is healthy. Prior data remains at /data/${prior_name} until an operator removes it."
curl --fail --silent --show-error "http://127.0.0.1:${public_port}/healthz"
echo
docker compose ps
