#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/deploy/env/api.env}"
REPO_DATA_ROOT="$PROJECT_ROOT/data"
RUNTIME_ROOT="$PROJECT_ROOT/runtime"
LOCK_ROOT="$RUNTIME_ROOT/locks"
TMP_ROOT="$RUNTIME_ROOT/tmp"
LOCK_FILE="$LOCK_ROOT/personal-homepage-publish.lock"
MKDIR_ERR_FILE="$TMP_ROOT/personal-homepage-publish-mkdir.err"
LOCK_MODE="none"
LOCK_DIR=""
BUILD_ROOT=""

mkdir -p "$LOCK_ROOT" "$TMP_ROOT"

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

finish() {
  local exit_code=$?

  if [[ -n "$BUILD_ROOT" && -d "$BUILD_ROOT" ]]; then
    rm -rf "$BUILD_ROOT"
  fi

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

DEFAULT_SITE_ROOT="/var/www/personal-homepage/frontend/dist"
FALLBACK_SITE_ROOT="$PROJECT_ROOT/runtime/site-preview"
if [[ -n "${SITE_ROOT+x}" && -n "${SITE_ROOT:-}" ]]; then
  SITE_ROOT_SOURCE="env"
else
  SITE_ROOT_SOURCE="default"
  SITE_ROOT="$DEFAULT_SITE_ROOT"
fi

cd "$PROJECT_ROOT"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

echo "Publish target candidate: $SITE_ROOT (source=$SITE_ROOT_SOURCE)"

if ! mkdir -p "$SITE_ROOT" 2>"$MKDIR_ERR_FILE"; then
  mkdir_error="$(cat "$MKDIR_ERR_FILE" || true)"

  if [[ "$SITE_ROOT_SOURCE" == "default" ]]; then
    echo "Default SITE_ROOT is not writable, fallback to local preview directory: $FALLBACK_SITE_ROOT"
    SITE_ROOT="$FALLBACK_SITE_ROOT"

    if ! mkdir -p "$SITE_ROOT" 2>"$MKDIR_ERR_FILE"; then
      fallback_error="$(cat "$MKDIR_ERR_FILE" || true)"
      echo "Failed(code=1): unable to create fallback SITE_ROOT '$SITE_ROOT'. $fallback_error"
      exit 1
    fi
  else
    echo "Failed(code=1): SITE_ROOT is explicitly configured but not writable: '$SITE_ROOT'. $mkdir_error"
    exit 1
  fi
fi

echo "Resolved publish directory: $SITE_ROOT"

CONTENT_DATA_ROOT="${DATA_ROOT:-$REPO_DATA_ROOT}"
BUILD_PARENT="$PROJECT_ROOT/runtime/build-workdir"
mkdir -p "$BUILD_PARENT"

if [[ "$CONTENT_DATA_ROOT" != "$REPO_DATA_ROOT" ]]; then
  if [[ ! -d "$CONTENT_DATA_ROOT" ]]; then
    echo "Failed(code=1): DATA_ROOT does not exist: $CONTENT_DATA_ROOT"
    exit 1
  fi

  BUILD_ROOT="$(mktemp -d "$BUILD_PARENT/publish.XXXXXX")"
  echo "Resolved external content data root: $CONTENT_DATA_ROOT"
  echo "Resolved temporary build root: $BUILD_ROOT"

  rsync -a \
    --delete \
    --exclude '.git/' \
    --exclude 'public/' \
    --exclude 'resources/' \
    --exclude 'runtime/build-workdir/' \
    --exclude 'runtime/hugo_cache/' \
    --exclude 'runtime/site-preview/' \
    --exclude 'api/.venv/' \
    "$PROJECT_ROOT/" "$BUILD_ROOT/"

  mkdir -p "$BUILD_ROOT/data"
  rsync -a --delete "$CONTENT_DATA_ROOT/" "$BUILD_ROOT/data/"
  BUILD_PROJECT_ROOT="$BUILD_ROOT"
else
  BUILD_PROJECT_ROOT="$PROJECT_ROOT"
fi

HUGO_CACHE_DIR="${HUGO_CACHE_DIR:-$PROJECT_ROOT/runtime/hugo_cache}"
mkdir -p "$HUGO_CACHE_DIR"
export HUGO_CACHEDIR="$HUGO_CACHE_DIR"
echo "Resolved Hugo cache directory: $HUGO_CACHEDIR"

"$BUILD_PROJECT_ROOT/scripts/check-template-asset-paths.sh"
(
  cd "$BUILD_PROJECT_ROOT"
  hugo --gc --minify
)

if ! rsync -a --delete "$BUILD_PROJECT_ROOT/public/" "$SITE_ROOT/"; then
  echo "Failed(code=1): rsync failed while publishing to '$SITE_ROOT'."
  exit 1
fi

if [[ "${RELOAD_NGINX:-false}" == "true" ]]; then
  systemctl reload nginx
fi
