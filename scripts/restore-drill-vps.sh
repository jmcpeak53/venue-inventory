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

drill_root="$(mktemp -d "${TMPDIR:-/tmp}/venue-inventory-restore-drill.XXXXXX")"
cleanup() {
  rm -rf "$drill_root"
}
trap cleanup EXIT

mkdir -p "$drill_root/data" "$drill_root/archive"
cp -f "$archive_host" "$drill_root/archive/backup.tar.gz"

echo "Restoring into isolated directory $drill_root/data (live /data is not written)"
docker compose run --rm --no-deps --entrypoint python \
  -v "$drill_root/archive/backup.tar.gz:/archive.tar.gz:ro" \
  -v "$drill_root/data:/isolated-data" \
  web -m app.backups restore-drill \
  --archive /archive.tar.gz \
  --target-data-dir /isolated-data \
  --owner 1000:1000

echo "Running readiness and representative HTTP smoke requests against isolated data"
docker compose run --rm --no-deps --entrypoint python \
  -v "$drill_root/data:/isolated-data" \
  -e VENUE_INVENTORY_DATA_DIR=/isolated-data \
  -e VENUE_INVENTORY_REQUIRE_DATA_MOUNT=false \
  web -c '
import os
from app import create_app
from app.config import AppConfig
from app.migrate import upgrade_to_head

config = AppConfig.from_environ(os.environ)
# Live restore migrates on startup. Apply the same upgrade so a pre-migration
# archive (the deploy-time backup) still becomes ready against this image.
upgrade_to_head(config.database_url)
application = create_app(config)
client = application.test_client()
for path, expected in (
    ("/healthz", 200),
    ("/readyz", 200),
    ("/", 200),
    ("/admin/login", 200),
    ("/customer/login", 200),
):
    response = client.get(path)
    if response.status_code != expected:
        raise SystemExit(f"{path} returned {response.status_code}")
print("isolated restore drill passed")
'

echo "Isolated restore drill reproduced application state without touching live data."
