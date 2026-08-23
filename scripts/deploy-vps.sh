#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

deploy_host="${DEPLOY_HOST:-prod-vps-01}"
repository="${DEPLOY_REPOSITORY:-https://github.com/jmcpeak53/venue-inventory.git}"
deploy_directory="${DEPLOY_DIRECTORY:-/opt/venue-inventory}"
deploy_ref="${DEPLOY_REF:-main}"
public_port="${VENUE_INVENTORY_PORT:-8080}"
required_checks="${VENUE_INVENTORY_REQUIRED_CHECKS:-verify}"
github_repo="${VENUE_INVENTORY_GITHUB_REPO:-jmcpeak53/venue-inventory}"
public_ipv4="${VENUE_INVENTORY_PUBLIC_IPV4:-5.78.222.116}"
skip_dns="${VENUE_INVENTORY_SKIP_DNS:-0}"
skip_ci="${VENUE_INVENTORY_SKIP_CI:-0}"

if ! [[ "$public_port" =~ ^[0-9]+$ ]] || ((public_port < 1 || public_port > 65535)); then
  echo "VENUE_INVENTORY_PORT must be a number from 1 through 65535." >&2
  exit 2
fi

github_token="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
if [[ -z "$github_token" ]] && command -v gh >/dev/null; then
  github_token="$(gh auth token 2>/dev/null || true)"
fi

target_sha="$(git ls-remote "$repository" "refs/heads/${deploy_ref}" | awk '{print $1}')"
if [[ -z "$target_sha" ]]; then
  echo "Deployment stopped: could not resolve ${deploy_ref} on ${repository}." >&2
  exit 1
fi

echo "Deploying ${repository} (${deploy_ref} ${target_sha}) to ${deploy_host}:${deploy_directory}"

if [[ "$skip_ci" != "1" ]]; then
  if [[ -z "$github_token" ]]; then
    echo "Deployment stopped: GitHub token missing for CI verification." >&2
    exit 1
  fi
  python3 "$root/scripts/deploy_lib.py" check-ci \
    --repo "$github_repo" \
    --sha "$target_sha" \
    --required "$required_checks" \
    --token "$github_token"
fi

if [[ "$skip_dns" != "1" ]]; then
  python3 "$root/scripts/deploy_lib.py" ensure-dns --content "$public_ipv4"
  echo "Waiting for inventory.needleminder.app to resolve to ${public_ipv4}"
  resolved=""
  for _attempt in $(seq 1 36); do
    resolved="$(dig +short A inventory.needleminder.app @1.1.1.1 | tail -n 1 || true)"
    if [[ "$resolved" == "$public_ipv4" ]]; then
      break
    fi
    sleep 10
  done
  if [[ "$resolved" != "$public_ipv4" ]]; then
    echo "Deployment stopped: inventory.needleminder.app does not yet resolve to ${public_ipv4}." >&2
    exit 1
  fi
  echo "DNS is ready."
fi

remote_fail_after="${VENUE_INVENTORY_DEPLOY_FAIL_AFTER:-}"
remote_skip_ingress="${VENUE_INVENTORY_SKIP_INGRESS:-0}"
remote_skip_firewall="${VENUE_INVENTORY_SKIP_FIREWALL:-0}"

ssh "$deploy_host" env \
  VENUE_INVENTORY_DEPLOY_FAIL_AFTER="$remote_fail_after" \
  VENUE_INVENTORY_SKIP_INGRESS="$remote_skip_ingress" \
  VENUE_INVENTORY_SKIP_FIREWALL="$remote_skip_firewall" \
  VENUE_INVENTORY_PORT="$public_port" \
  bash -s -- "$repository" "$deploy_directory" "$deploy_ref" <<'REMOTE_SCRIPT'
set -euo pipefail

repository="$1"
deploy_directory="$2"
deploy_ref="$3"

if [[ -d "$deploy_directory/.git" ]]; then
  cd "$deploy_directory"

  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Deployment stopped: $deploy_directory contains uncommitted changes." >&2
    exit 1
  fi

  prior_sha="$(git rev-parse HEAD)"
  git fetch origin "$deploy_ref"
  target_sha="$(git rev-parse FETCH_HEAD)"

  if [[ "$prior_sha" != "$target_sha" ]] && ! git merge-base --is-ancestor "$prior_sha" "$target_sha"; then
    echo "Deployment stopped: $deploy_directory is not a fast-forward to ${deploy_ref}." >&2
    exit 1
  fi

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
  prior_sha="$(git rev-parse HEAD)"
fi

if [[ ! -x ./scripts/deploy-remote.sh ]]; then
  echo "Deployment stopped: scripts/deploy-remote.sh is missing from the target revision." >&2
  exit 1
fi

export PRIOR_HEALTHY_SHA="$prior_sha"
exec ./scripts/deploy-remote.sh
REMOTE_SCRIPT

echo
if ssh "$deploy_host" "test -f /etc/venue-inventory/admin-password-once"; then
  once="$(ssh "$deploy_host" "cat /etc/venue-inventory/admin-password-once")"
  ssh "$deploy_host" "rm -f /etc/venue-inventory/admin-password-once"
  echo "Operator handoff"
  echo "URL: https://inventory.needleminder.app/"
  echo "Administrator password (shown once): ${once}"
else
  echo "Operator handoff"
  echo "URL: https://inventory.needleminder.app/"
  echo "Administrator password was already configured and is not shown again."
fi
