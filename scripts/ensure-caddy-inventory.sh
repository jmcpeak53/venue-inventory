#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

caddyfile="${VENUE_INVENTORY_CADDYFILE:-/apps/needle-minder/Caddyfile}"
caddy_container="${VENUE_INVENTORY_CADDY_CONTAINER:-caddy-needle-minder}"
hostname="${VENUE_INVENTORY_HOSTNAME:-inventory.needleminder.app}"
upstream="${VENUE_INVENTORY_UPSTREAM:-venue-inventory:8080}"
needleminder_url="${NEEDLEMINDER_URL:-https://needleminder.app/}"

if [[ ! -f "$caddyfile" ]]; then
  echo "Caddyfile not found at $caddyfile" >&2
  exit 1
fi

check_needleminder() {
  local label="$1"
  if ! curl --fail --silent --show-error --max-time 20 -o /dev/null "$needleminder_url"; then
    echo "Needleminder is not healthy ${label}." >&2
    return 1
  fi
  echo "Needleminder is healthy ${label}."
}

check_needleminder "before Caddy changes"

python3 "$root/scripts/deploy_lib.py" patch-caddyfile \
  --input "$caddyfile" \
  --output /tmp/venue-inventory.Caddyfile \
  --hostname "$hostname" \
  --upstream "$upstream" \
  2>/tmp/venue-inventory.caddy-patch-status

if [[ "$(tr -d '[:space:]' </tmp/venue-inventory.caddy-patch-status)" == "unchanged" ]] \
  && cmp -s "$caddyfile" /tmp/venue-inventory.Caddyfile; then
  echo "Caddy inventory block already present; leaving $caddyfile unchanged."
  echo "caddy_backup="
  check_needleminder "after skipped Caddy changes"
  exit 0
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup="${caddyfile}.bak-venue-inventory-${stamp}"
cp -a "$caddyfile" "$backup"
echo "Backed up Caddyfile to $backup"
cp /tmp/venue-inventory.Caddyfile "$caddyfile"

restore_caddyfile() {
  echo "Restoring Caddyfile from $backup" >&2
  cp -a "$backup" "$caddyfile"
  docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile >/dev/null || true
}

if ! docker exec "$caddy_container" caddy validate --config /etc/caddy/Caddyfile >/tmp/venue-inventory.caddy-validate 2>&1; then
  cat /tmp/venue-inventory.caddy-validate >&2
  restore_caddyfile
  echo "Caddy configuration validation failed; original file restored." >&2
  exit 1
fi

if ! docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile >/tmp/venue-inventory.caddy-reload 2>&1; then
  cat /tmp/venue-inventory.caddy-reload >&2
  restore_caddyfile
  echo "Caddy reload failed; original file restored." >&2
  exit 1
fi

if ! check_needleminder "after Caddy reload"; then
  restore_caddyfile
  check_needleminder "after Caddy rollback" || true
  exit 1
fi

echo "caddy_backup=${backup}"
echo "Caddy is serving ${hostname} through ${upstream}."
