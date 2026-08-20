#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
cd "$ROOT_DIR"

OUTPUT_DIR="${1:-dist/cdp-helper}"
command -v uv >/dev/null 2>&1 || {
  echo "uv is required to build the standalone CDP helper." >&2
  exit 1
}

uv tool run --python 3.12 --from pyinstaller pyinstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name nanobot-cdp-helper \
  --paths "$ROOT_DIR" \
  --workpath "dist/cdp-helper-build" \
  --specpath "dist/cdp-helper-build" \
  --distpath "$OUTPUT_DIR" \
  scripts/cdp-helper-entrypoint.py
