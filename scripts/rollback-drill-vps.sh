#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

drill_root="${VENUE_INVENTORY_DRILL_ROOT:-/tmp/venue-inventory-rollback-drill}"
drill_port="${VENUE_INVENTORY_DRILL_PORT:-18081}"
project="${VENUE_INVENTORY_DRILL_PROJECT:-venue-inventory-rollback-drill}"

cleanup() {
  docker compose -p "$project" -f "$drill_root/compose.yaml" down --remove-orphans -v >/dev/null 2>&1 || true
  rm -rf "$drill_root"
}
trap cleanup EXIT

rm -rf "$drill_root"
mkdir -p "$drill_root"
git clone --quiet "$root" "$drill_root"
cd "$drill_root"

export COMPOSE_PROJECT_NAME="$project"
export VENUE_INVENTORY_PORT="$drill_port"
export VENUE_INVENTORY_BIND_ADDRESS="127.0.0.1"
export VENUE_INVENTORY_BACKUP_DIR="$drill_root/backups"
export VENUE_INVENTORY_SKIP_INGRESS=1
export VENUE_INVENTORY_SKIP_FIREWALL=1
export PRIOR_HEALTHY_SHA="$(git rev-parse HEAD)"

echo "Starting isolated rollback drill in $drill_root"
docker compose -p "$project" -f compose.yaml up -d --build --wait --wait-timeout 60
curl --fail --silent --show-error "http://127.0.0.1:${drill_port}/readyz" >/dev/null

VENUE_INVENTORY_BACKUP_USE_RUN=1 ./scripts/run-backup-vps.sh >/tmp/venue-inventory-drill-backup.out
archive="$(python3 "$drill_root/scripts/deploy_lib.py" parse-backup-path --backup-dir "$drill_root/backups" </tmp/venue-inventory-drill-backup.out)"
if [[ -z "$archive" || ! -f "$archive" ]]; then
  echo "Rollback drill could not locate the paired backup archive." >&2
  exit 1
fi

echo "Inducing a readiness failure against the isolated copy"
if VENUE_INVENTORY_DEPLOY_FAIL_AFTER=readiness \
  VENUE_INVENTORY_PORT="$drill_port" \
  PRIOR_HEALTHY_SHA="$PRIOR_HEALTHY_SHA" \
  ./scripts/deploy-remote.sh; then
  echo "Rollback drill failed: induced failure did not stop the deployment." >&2
  exit 1
fi

curl --fail --silent --show-error "http://127.0.0.1:${drill_port}/healthz" >/dev/null
curl --fail --silent --show-error "http://127.0.0.1:${drill_port}/readyz" >/dev/null
current_sha="$(git rev-parse HEAD)"
if [[ "$current_sha" != "$PRIOR_HEALTHY_SHA" ]]; then
  echo "Rollback drill failed: checkout is $current_sha, expected $PRIOR_HEALTHY_SHA." >&2
  exit 1
fi

echo "Isolated rollback drill restored ${current_sha} and verified health."
