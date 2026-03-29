#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DOUBLE_QUOTE_PATTERN='\{\{\s*" /'
SINGLE_QUOTE_PATTERN="\{\{\s*' /"

if rg -n "$DOUBLE_QUOTE_PATTERN" layouts >/dev/null 2>&1 || rg -n "$SINGLE_QUOTE_PATTERN" layouts >/dev/null 2>&1; then
  echo "Invalid template asset path detected: remove the leading space after opening quote."
  rg -n "$DOUBLE_QUOTE_PATTERN" layouts || true
  rg -n "$SINGLE_QUOTE_PATTERN" layouts || true
  exit 1
fi

echo "Template asset path check passed."
