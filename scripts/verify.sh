#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

in_container=0
if [[ "${1:-}" == "--in-container" ]]; then
  in_container=1
elif [[ -f /.dockerenv ]]; then
  in_container=1
fi

run_checks() {
  echo "==> Formatting"
  ruff format --check .
  echo "==> Linting"
  ruff check .
  echo "==> Tests"
  pytest -q
  echo "==> In-container checks passed"
}

if [[ "$in_container" -eq 1 ]]; then
  run_checks
  exit 0
fi

project="${VENUE_INVENTORY_VERIFY_PROJECT:-venue-inventory-verify}"
port="${VENUE_INVENTORY_VERIFY_PORT:-18080}"

echo "==> Running formatting, lint, tests, and migration checks"
docker compose run --build --rm --no-deps verify --in-container

echo "==> Building and probing the application container"
export VENUE_INVENTORY_PORT="$port"
export VENUE_INVENTORY_BIND_ADDRESS="127.0.0.1"

cleanup() {
  docker compose -p "$project" down --remove-orphans -v >/dev/null 2>&1 || true
}
trap cleanup EXIT

if ! docker compose -p "$project" up --build --wait --wait-timeout 60; then
  echo "Application container did not become healthy." >&2
  docker compose -p "$project" ps >&2 || true
  docker compose -p "$project" logs --tail=100 web >&2 || true
  exit 1
fi

liveness="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/healthz")"
readiness="$(curl --fail --silent --show-error "http://127.0.0.1:${port}/readyz")"
if [[ "$liveness" != "ok" || "$readiness" != "ok" ]]; then
  echo "Health endpoints did not return the expected body." >&2
  printf 'healthz: %q\n' "$liveness" >&2
  printf 'readyz: %q\n' "$readiness" >&2
  exit 1
fi

echo "==> Recreating the container to confirm persistent data"
docker compose -p "$project" up --force-recreate --wait --wait-timeout 60
curl --fail --silent --show-error "http://127.0.0.1:${port}/readyz" >/dev/null
docker compose -p "$project" exec -T web python -c \
  "from pathlib import Path; p = Path('/data/venue-inventory.sqlite3'); assert p.is_file() and p.stat().st_size > 0, p"

echo "==> Confirming the production image does not include verification tooling"
docker compose -p "$project" exec -T web python -c \
  "import importlib.util; from pathlib import Path; assert not Path('/app/tests').exists(); assert not Path('/app/scripts/verify.sh').exists(); assert importlib.util.find_spec('pytest') is None; assert importlib.util.find_spec('ruff') is None"

echo "==> Verification passed"
