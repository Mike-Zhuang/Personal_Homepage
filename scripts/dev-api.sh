#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/api"
VENV_DIR="$API_DIR/.venv"

load_admin_api_key_if_needed() {
  if [[ -n "${ADMIN_API_KEY:-}" ]]; then
    return
  fi

  local env_file=""
  local key_value=""
  local -a candidates=(
    "$API_DIR/.env"
    "$ROOT_DIR/deploy/env/api.env"
  )

  for env_file in "${candidates[@]}"; do
    if [[ ! -f "$env_file" ]]; then
      continue
    fi

    key_value="$(sed -n -E 's/^[[:space:]]*ADMIN_API_KEY[[:space:]]*=[[:space:]]*//p' "$env_file" | tail -n 1)"
    key_value="${key_value%$'\r'}"

    if [[ -z "$key_value" ]]; then
      continue
    fi

    if [[ "$key_value" == \"*\" && "$key_value" == *\" ]]; then
      key_value="${key_value#\"}"
      key_value="${key_value%\"}"
    elif [[ "$key_value" == \'*\' && "$key_value" == *\' ]]; then
      key_value="${key_value#\'}"
      key_value="${key_value%\'}"
    fi

    if [[ -n "$key_value" ]]; then
      export ADMIN_API_KEY="$key_value"
      echo "Loaded ADMIN_API_KEY from $env_file"
      return
    fi
  done

  echo "Warning: ADMIN_API_KEY is not set. Put it in api/.env or deploy/env/api.env."
}

if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="$(command -v python)"
else
  echo "Python is required but was not found in PATH."
  exit 127
fi

cd "$API_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

load_admin_api_key_if_needed

if ! python -c "import fastapi, uvicorn, tomli_w" >/dev/null 2>&1; then
  python -m pip install -r requirements.txt
fi

export PYTHONUNBUFFERED=1

UVICORN_LOG_LEVEL="${UVICORN_LOG_LEVEL:-info}"
UVICORN_ACCESS_LOG="${UVICORN_ACCESS_LOG:-true}"
UVICORN_RELOAD="${UVICORN_RELOAD:-true}"

to_lower() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

UVICORN_ARGS=(
  app.main:app
  --host 127.0.0.1
  --port 8000
  --log-level "$UVICORN_LOG_LEVEL"
)

if [[ "$(to_lower "$UVICORN_ACCESS_LOG")" == "true" ]]; then
  UVICORN_ARGS+=(--access-log)
fi

if [[ "$(to_lower "$UVICORN_RELOAD")" == "true" ]]; then
  UVICORN_ARGS+=(--reload)
fi

if [[ -n "${API_DEV_LOG_FILE:-}" ]]; then
  LOG_FILE_PATH="${API_DEV_LOG_FILE}"
  LOG_FILE_DIR="$(dirname "$LOG_FILE_PATH")"
  mkdir -p "$LOG_FILE_DIR"
  echo "Streaming backend logs to terminal and $LOG_FILE_PATH"

  set +e
  uvicorn "${UVICORN_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE_PATH"
  uvicorn_status=${PIPESTATUS[0]}
  set -e
  exit "$uvicorn_status"
fi

exec uvicorn "${UVICORN_ARGS[@]}"
