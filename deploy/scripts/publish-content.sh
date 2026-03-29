#!/usr/bin/env bash

set -euo pipefail

LOCK_FILE="/tmp/personal-homepage-publish.lock"
LOCK_MODE="none"
LOCK_DIR=""

if command -v flock >/dev/null 2>&1; then
  exec 200>"$LOCK_FILE"
  if ! flock -n 200; then
    echo "Failed(code=11): another publish task is still running."
    exit 11
  fi
  LOCK_MODE="flock"
else
  LOCK_DIR="${LOCK_FILE}.d"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "Failed(code=11): another publish task is still running."
    exit 11
  fi
  LOCK_MODE="mkdir"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

finish() {
  local exit_code=$?

  if [[ "$LOCK_MODE" == "mkdir" && -n "$LOCK_DIR" ]]; then
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi

  if [[ $exit_code -eq 0 ]]; then
    echo "Successful"
  else
    echo "Failed(code=$exit_code)"
  fi
  exit "$exit_code"
}
trap finish EXIT

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
DEFAULT_SITE_ROOT="/var/www/personal-homepage/frontend/dist"
FALLBACK_SITE_ROOT="$PROJECT_ROOT/runtime/site-preview"
if [[ -n "${SITE_ROOT+x}" && -n "${SITE_ROOT:-}" ]]; then
  SITE_ROOT_SOURCE="env"
else
  SITE_ROOT_SOURCE="default"
  SITE_ROOT="$DEFAULT_SITE_ROOT"
fi

cd "$PROJECT_ROOT"

echo "Publish target candidate: $SITE_ROOT (source=$SITE_ROOT_SOURCE)"

if ! mkdir -p "$SITE_ROOT" 2>/tmp/personal-homepage-publish-mkdir.err; then
  mkdir_error="$(cat /tmp/personal-homepage-publish-mkdir.err || true)"

  if [[ "$SITE_ROOT_SOURCE" == "default" ]]; then
    echo "Default SITE_ROOT is not writable, fallback to local preview directory: $FALLBACK_SITE_ROOT"
    SITE_ROOT="$FALLBACK_SITE_ROOT"

    if ! mkdir -p "$SITE_ROOT" 2>/tmp/personal-homepage-publish-mkdir.err; then
      fallback_error="$(cat /tmp/personal-homepage-publish-mkdir.err || true)"
      echo "Failed(code=1): unable to create fallback SITE_ROOT '$SITE_ROOT'. $fallback_error"
      exit 1
    fi
  else
    echo "Failed(code=1): SITE_ROOT is explicitly configured but not writable: '$SITE_ROOT'. $mkdir_error"
    exit 1
  fi
fi

echo "Resolved publish directory: $SITE_ROOT"

"$PROJECT_ROOT/scripts/check-template-asset-paths.sh"
hugo --gc --minify

if ! rsync -a --delete "$PROJECT_ROOT/public/" "$SITE_ROOT/"; then
  echo "Failed(code=1): rsync failed while publishing to '$SITE_ROOT'."
  exit 1
fi

if [[ "${RELOAD_NGINX:-false}" == "true" ]]; then
  systemctl reload nginx
fi
