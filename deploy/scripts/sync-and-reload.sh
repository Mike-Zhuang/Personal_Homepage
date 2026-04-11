#!/usr/bin/env bash

set -euo pipefail

LOCK_FILE="/tmp/personal-homepage-sync.lock"
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

PROJECT_ROOT="${PROJECT_ROOT:-/opt/personal-homepage}"
BRANCH="${BRANCH:-main}"
SITE_ROOT="${SITE_ROOT:-/var/www/personal-homepage/frontend/dist}"
API_SERVICE="${API_SERVICE:-personal-homepage-api}"
HUGO_BIN="${HUGO_BIN:-/usr/local/bin/hugo}"

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

"$HUGO_BIN" --gc --minify

mkdir -p "$SITE_ROOT"
rsync -a --delete "$PROJECT_ROOT/public/" "$SITE_ROOT/"

systemctl restart "$API_SERVICE"
systemctl reload nginx

curl -fsS "http://127.0.0.1:8000/api/health" >/dev/null
