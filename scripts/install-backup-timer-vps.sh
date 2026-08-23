#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deploy_directory="${DEPLOY_DIRECTORY:-/opt/venue-inventory}"
unit_dir="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
service_source="$root/systemd/venue-inventory-backup.service"
timer_source="$root/systemd/venue-inventory-backup.timer"
service_destination="$unit_dir/venue-inventory-backup.service"
timer_destination="$unit_dir/venue-inventory-backup.timer"

render_unit() {
  local source="$1"
  local destination="$2"
  local rendered
  rendered="$(mktemp)"
  sed "s|@DEPLOY_DIRECTORY@|${deploy_directory}|g" "$source" >"$rendered"
  if [[ -f "$destination" ]] && sudo cmp -s "$rendered" "$destination"; then
    rm -f "$rendered"
    return 0
  fi
  sudo install -m 0644 "$rendered" "$destination"
  rm -f "$rendered"
}

render_unit "$service_source" "$service_destination"
render_unit "$timer_source" "$timer_destination"
sudo systemctl daemon-reload
sudo systemctl enable --now venue-inventory-backup.timer
echo "Installed venue-inventory-backup.timer for ${deploy_directory}."
