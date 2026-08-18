#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-/opt/personal-homepage}"
RUNTIME_ROOT="$PROJECT_ROOT/runtime"
ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/deploy/env/api.env}"
REPO_DATA_ROOT="$PROJECT_ROOT/data"
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

reload_nginx() {
  if systemctl is-active --quiet nginx 2>/dev/null; then
    systemctl reload nginx
    return 0
  fi

  if [[ -x /etc/init.d/nginx ]]; then
    /etc/init.d/nginx reload
    return 0
  fi

  echo "Warning: nginx reload skipped (service manager not detected)."
}

wait_for_api_health() {
  local attempt=1
  local retry_delays=(1 2 3 5)
  local total_attempts=$(( ${#retry_delays[@]} + 1 ))

  while true; do
    if curl -fsS "$API_HEALTH_URL" >/dev/null; then
      return 0
    fi

    if (( attempt >= total_attempts )); then
      echo "Failed(code=7): API health check failed after ${attempt} attempts: $API_HEALTH_URL"
      return 1
    fi

    sleep "${retry_delays[$((attempt - 1))]}"
    attempt=$((attempt + 1))
  done
}

BRANCH="${BRANCH:-main}"
SITE_ROOT="${SITE_ROOT:-/var/www/personal-homepage/frontend/dist}"
API_SERVICE="${API_SERVICE:-personal-homepage-api}"
HUGO_BIN="${HUGO_BIN:-/usr/local/bin/hugo}"
PUBLISH_SCRIPT="${PUBLISH_SCRIPT:-$PROJECT_ROOT/deploy/scripts/publish-content.sh}"
API_HEALTH_URL="${API_HEALTH_URL:-http://127.0.0.1:8001/api/health}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

CONTENT_DATA_ROOT="${DATA_ROOT:-$REPO_DATA_ROOT}"

if [[ ! -x "$HUGO_BIN" ]]; then
  HUGO_BIN="$(command -v hugo || true)"
fi

if [[ -z "$HUGO_BIN" ]]; then
  echo "Failed(code=127): hugo executable not found."
  exit 127
fi

cd "$PROJECT_ROOT"

current_head="$(git rev-parse HEAD)"
git fetch origin "$BRANCH"
fetched_head="$(git rev-parse FETCH_HEAD)"

if [[ "$current_head" == "$fetched_head" ]]; then
  echo "No upstream code changes detected on '$BRANCH'; skip publish and service reload."
  exit 0
fi

changed_data_files=()
while IFS= read -r changed_path; do
  [[ -n "$changed_path" ]] && changed_data_files+=("$changed_path")
done < <(git diff --name-only "$current_head" "$fetched_head" -- 'data/*.toml')

data_sync_backup=""
if [[ "$CONTENT_DATA_ROOT" != "$REPO_DATA_ROOT" && ${#changed_data_files[@]} -gt 0 ]]; then
  for changed_path in "${changed_data_files[@]}"; do
    data_name="${changed_path#data/}"
    repo_file="$REPO_DATA_ROOT/$data_name"
    live_file="$CONTENT_DATA_ROOT/$data_name"

    if [[ ! -f "$repo_file" || ! -f "$live_file" ]] || ! cmp -s "$repo_file" "$live_file"; then
      echo "Failed(code=12): repository data '$changed_path' changed upstream, but '$live_file' has independent content."
      echo "Merge the repository update into live-data before deploying templates; refusing a mixed-version publish."
      exit 12
    fi
  done

  data_sync_backup="$RUNTIME_ROOT/backups/live-data-auto-sync-$(date +%Y%m%d%H%M%S)"
  mkdir -p "$data_sync_backup"
  for changed_path in "${changed_data_files[@]}"; do
    data_name="${changed_path#data/}"
    cp -a "$CONTENT_DATA_ROOT/$data_name" "$data_sync_backup/$data_name"
  done
  echo "Prepared live-data backup before repository sync: $data_sync_backup"
fi

git pull --ff-only origin "$BRANCH"

if [[ -n "$data_sync_backup" ]]; then
  for changed_path in "${changed_data_files[@]}"; do
    data_name="${changed_path#data/}"
    cp "$REPO_DATA_ROOT/$data_name" "$CONTENT_DATA_ROOT/$data_name"
  done
  echo "Synchronized changed repository data into live-data after fast-forward pull."
fi

if [[ ! -x "$PUBLISH_SCRIPT" ]]; then
  echo "Failed(code=127): publish script not found or not executable: $PUBLISH_SCRIPT"
  exit 127
fi

SITE_ROOT="$SITE_ROOT" RELOAD_NGINX=false HUGO_BIN="$HUGO_BIN" "$PUBLISH_SCRIPT"

systemctl restart "$API_SERVICE"
reload_nginx

wait_for_api_health
