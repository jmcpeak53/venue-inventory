#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

secrets_dir="${VENUE_INVENTORY_SECRETS_DIR:-/etc/venue-inventory}"
secrets_file="${VENUE_INVENTORY_SECRETS_FILE:-$secrets_dir/prod.env}"
hash_file="${VENUE_INVENTORY_ADMIN_HASH_FILE:-$secrets_dir/admin-password.hash}"
once_file="${VENUE_INVENTORY_ADMIN_PASSWORD_ONCE_FILE:-$secrets_dir/admin-password-once}"

mkdir -p "$secrets_dir"
chmod 700 "$secrets_dir"

if [[ -f "$secrets_file" && -f "$hash_file" ]]; then
  echo "Production secrets already exist at $secrets_dir; leaving them unchanged."
  exit 0
fi

if [[ -f "$secrets_file" || -f "$hash_file" ]]; then
  echo "Production secrets are incomplete. Refusing to overwrite a partial set at $secrets_dir." >&2
  exit 1
fi

echo "Generating production secrets in $secrets_dir"
tmp="$(mktemp)"
chmod 600 "$tmp"
cleanup() { rm -f "$tmp"; }
trap cleanup EXIT

COMPOSE_PROGRESS=quiet docker compose -f compose.yaml run --rm --no-deps --no-log-prefix \
  --entrypoint python web -m app.hash_password --bootstrap-json >"$tmp"

python3 - "$tmp" "$secrets_file" "$hash_file" "$once_file" <<'PY'
import json
import os
import sys
from pathlib import Path

raw_path, secrets_file, hash_file, once_file = sys.argv[1:]
raw = Path(raw_path).read_text(encoding="utf-8")
line = next((item for item in raw.splitlines() if item.startswith("{")), "")
if not line:
    raise SystemExit("bootstrap did not print a JSON secrets payload.")
payload = json.loads(line)
os.umask(0o077)
Path(secrets_file).write_text(payload["env"], encoding="utf-8")
os.chmod(secrets_file, 0o600)
Path(hash_file).write_text(payload["hash"] + "\n", encoding="utf-8")
os.chmod(hash_file, 0o600)
Path(once_file).write_text(payload["password"] + "\n", encoding="utf-8")
os.chmod(once_file, 0o600)
PY

echo "Wrote $secrets_file and $hash_file."
