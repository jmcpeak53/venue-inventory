#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

retention_days="${VENUE_INVENTORY_BACKUP_RETENTION_DAYS:-14}"
host_backup_dir="${VENUE_INVENTORY_BACKUP_DIR:-$root/backups}"
mkdir -p "$host_backup_dir"
git_sha="${VENUE_INVENTORY_DEPLOYED_GIT_SHA:-}"
if [[ -z "$git_sha" ]]; then
  git_sha="$(git rev-parse HEAD)"
fi

run_module() {
  if [[ "${VENUE_INVENTORY_BACKUP_USE_RUN:-}" == "1" ]] \
    || ! docker compose ps --status running --services 2>/dev/null | grep -qx web; then
    docker compose run --rm --no-deps --entrypoint python \
      -e VENUE_INVENTORY_DEPLOYED_GIT_SHA="$git_sha" \
      web -m app.backups "$@"
  else
    docker compose exec -T \
      -e VENUE_INVENTORY_DEPLOYED_GIT_SHA="$git_sha" \
      web python -m app.backups "$@"
  fi
}

echo "Creating verified backup of /data into /backups"
run_module backup --data-dir /data --backup-root /backups --git-sha "$git_sha"
echo "Pruning recognized backups older than ${retention_days} days"
run_module prune --backup-root /backups --retention-days "$retention_days"
