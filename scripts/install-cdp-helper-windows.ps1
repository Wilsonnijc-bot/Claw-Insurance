$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
  throw "This installer is for Windows. Use scripts/install-cdp-helper-macos.sh on macOS or scripts/install-cdp-helper-linux.sh on Linux."
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python -and -not $PyLauncher) {
  throw "Python is required for the host CDP helper installer."
}

$Code = @'
from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))

from nanobot.windows_cdp_helper import (
    DEFAULT_HELPER_URL,
    install_windows_helper,
    load_or_create_helper_token,
    resolve_chrome_path,
)


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
token = load_or_create_helper_token()
result = install_windows_helper(helper_token=token)

values = {
    "WEB_CDP_URL": "http://host.docker.internal:9222",
    "WEB_CDP_HELPER_URL": "http://host.docker.internal:9230",
    "WEB_CDP_HELPER_TOKEN": token,
    "WEB_CDP_HELPER_PLATFORM": "windows",
    "WEB_HOST_PROFILE_DIR": str(root / "whatsapp-web"),
    "WEB_HISTORY_SYNC_ENABLED": "true",
}
update_env_file(root / ".env", values)

print("Host CDP helper installed and started for Windows.")
print(f"Helper health URL on host: {DEFAULT_HELPER_URL}/healthz")
print(f"Scheduled task: {result['task_name']}")
print("Docker Compose will read these values from .env:")
for key, value in values.items():
    print(f"  {key}={value}")
print("Restart Docker Compose after changing host CDP helper settings.")
'@

$TempScript = Join-Path ([System.IO.Path]::GetTempPath()) ("nanobot-cdp-helper-" + [System.Guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $TempScript -Value $Code -Encoding UTF8

try {
  if ($PyLauncher) {
    & $PyLauncher.Source -3 $TempScript $RootDir
    $ExitCode = $LASTEXITCODE
  } else {
    & $Python.Source $TempScript $RootDir
    $ExitCode = $LASTEXITCODE
  }
  if ($ExitCode -ne 0) {
    exit $ExitCode
  }
} finally {
  Remove-Item -Force $TempScript -ErrorAction SilentlyContinue
}
