#!/usr/bin/env bash

set -euo pipefail

deploy_host="${DEPLOY_HOST:-prod-vps-01}"
repository="${DEPLOY_REPOSITORY:-https://github.com/jmcpeak53/venue-inventory.git}"
deploy_directory="${DEPLOY_DIRECTORY:-/opt/venue-inventory}"
deploy_ref="${DEPLOY_REF:-main}"
public_port="${VENUE_INVENTORY_PORT:-8080}"

if ! [[ "$public_port" =~ ^[0-9]+$ ]] || (( public_port < 1 || public_port > 65535 )); then
  echo "VENUE_INVENTORY_PORT must be a number from 1 through 65535." >&2
  exit 2
fi

echo "Deploying ${repository} (${deploy_ref}) to ${deploy_host}:${deploy_directory}"

ssh "$deploy_host" bash -s -- "$repository" "$deploy_directory" "$deploy_ref" "$public_port" <<'REMOTE_SCRIPT'
set -euo pipefail

repository="$1"
deploy_directory="$2"
deploy_ref="$3"
public_port="$4"

if [[ -d "$deploy_directory/.git" ]]; then
  cd "$deploy_directory"

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Deployment stopped: $deploy_directory contains uncommitted changes." >&2
    exit 1
  fi

  git fetch origin "$deploy_ref"

  if git show-ref --verify --quiet "refs/heads/$deploy_ref"; then
    git switch "$deploy_ref"
    git merge --ff-only FETCH_HEAD
  else
    git switch --create "$deploy_ref" FETCH_HEAD
  fi
elif [[ -e "$deploy_directory" ]]; then
  echo "Deployment stopped: $deploy_directory exists but is not a Git checkout." >&2
  exit 1
else
  mkdir -p "$(dirname "$deploy_directory")"
  git clone --branch "$deploy_ref" --single-branch "$repository" "$deploy_directory"
  cd "$deploy_directory"
fi

if ! VENUE_INVENTORY_PORT="$public_port" docker compose up -d --build --remove-orphans --wait --wait-timeout 60; then
  echo "Deployment did not become healthy within 60 seconds." >&2
  docker compose ps >&2
  docker compose logs --tail=100 web >&2
  exit 1
fi

curl --fail --silent --show-error "http://127.0.0.1:${public_port}/healthz" >/dev/null
echo "Deployment is healthy on port ${public_port}."
docker compose ps
REMOTE_SCRIPT
