#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT_DIR/api"
VENV_DIR="$API_DIR/.venv"

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

if ! python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  python -m pip install -r requirements.txt
fi

exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
