#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-/opt/personal-homepage}"
RUNTIME_ROOT="$PROJECT_ROOT/runtime"
LOCK_ROOT="$RUNTIME_ROOT/locks"
LOCK_FILE="$LOCK_ROOT/personal-homepage-sync.lock"

mkdir -p "$LOCK_ROOT"
chmod 0777 "$LOCK_ROOT"
touch "$LOCK_FILE"
chmod 0666 "$LOCK_FILE"

exec 200>"$LOCK_FILE"
if ! flock -n 200; then
  echo "Failed(code=11): another sync task is still running."
  exit 11
fi

finish() {
  local exit_code=$?
  if [[ $exit_code -eq 0 ]]; then
    echo "Successful"
  else
    echo "Failed(code=$exit_code)"
  fi
  exit "$exit_code"
}
trap finish EXIT

BRANCH="${BRANCH:-main}"
SITE_ROOT="${SITE_ROOT:-/var/www/personal-homepage/frontend/dist}"
API_SERVICE="${API_SERVICE:-personal-homepage-api}"
HUGO_BIN="${HUGO_BIN:-/usr/local/bin/hugo}"
PUBLISH_SCRIPT="${PUBLISH_SCRIPT:-$PROJECT_ROOT/deploy/scripts/publish-content.sh}"

if [[ ! -x "$HUGO_BIN" ]]; then
  HUGO_BIN="$(command -v hugo || true)"
fi

if [[ -z "$HUGO_BIN" ]]; then
  echo "Failed(code=127): hugo executable not found."
  exit 127
fi

cd "$PROJECT_ROOT"

git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"

if [[ ! -x "$PUBLISH_SCRIPT" ]]; then
  echo "Failed(code=127): publish script not found or not executable: $PUBLISH_SCRIPT"
  exit 127
fi

SITE_ROOT="$SITE_ROOT" RELOAD_NGINX=false "$PUBLISH_SCRIPT"

systemctl restart "$API_SERVICE"
systemctl reload nginx

curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null
