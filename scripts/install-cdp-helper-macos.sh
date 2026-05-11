#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "This installer is for macOS. Use scripts/install-cdp-helper-linux.sh on Linux." >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "python3 is required for the host CDP helper installer." >&2
  exit 1
fi

"$PYTHON_BIN" - "$ROOT_DIR" <<'PY'
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from nanobot.macos_cdp_helper import DEFAULT_HELPER_URL, install_launchd_helper, resolve_chrome_path


def update_env_file(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    managed = set(values)
    kept = [
        line
        for line in existing
        if not any(line.startswith(f"{key}=") for key in managed)
    ]
    kept.extend(f"{key}={value}" for key, value in values.items())
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


resolve_chrome_path(os.environ.get("WEB_CDP_CHROME_PATH", ""))
result = install_launchd_helper()

values = {
    "WEB_CDP_URL": "http://host.docker.internal:9222",
    "WEB_CDP_HELPER_URL": "http://host.docker.internal:9230",
    "WEB_CDP_HELPER_TOKEN": "",
    "WEB_CDP_HELPER_PLATFORM": "macos",
    "WEB_HOST_PROFILE_DIR": str(root / "whatsapp-web"),
    "WEB_HISTORY_SYNC_ENABLED": "true",
}
update_env_file(root / ".env", values)

print("Host CDP helper installed and started for macOS.")
print(f"Helper health URL on host: {DEFAULT_HELPER_URL}/healthz")
print(f"LaunchAgent: {result['launch_agent']}")
print("Docker Compose will read these values from .env:")
for key, value in values.items():
    print(f"  {key}={value}")
print("Restart Docker Compose after changing host CDP helper settings.")
PY
