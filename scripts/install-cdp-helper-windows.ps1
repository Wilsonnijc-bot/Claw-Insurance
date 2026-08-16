$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
  throw "This installer is for Windows. Use scripts/install-cdp-helper-macos.sh on macOS or scripts/install-cdp-helper-linux.sh on Linux."
}

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path

function Test-PythonRuntime {
  param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [string[]]$PrefixArgs = @()
  )

  try {
    & $Executable @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
    return $LASTEXITCODE -eq 0
  } catch {
    return $false
  }
}

function Add-PythonCandidate {
  param(
    [System.Collections.Generic.List[object]]$Candidates,
    [string]$Executable,
    [string[]]$PrefixArgs = @()
  )

  if (-not $Executable) {
    return
  }
  $key = "$Executable|$($PrefixArgs -join ' ')"
  if ($Candidates | Where-Object { $_.Key -eq $key }) {
    return
  }
  $Candidates.Add([pscustomobject]@{
    Key = $key
    Executable = $Executable
    PrefixArgs = $PrefixArgs
  })
}

$PythonCandidates = [System.Collections.Generic.List[object]]::new()
foreach ($commandName in @("python", "python3")) {
  $command = Get-Command $commandName -ErrorAction SilentlyContinue
  if ($command) {
    Add-PythonCandidate $PythonCandidates $command.Source
  }
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($PyLauncher) {
  Add-PythonCandidate $PythonCandidates $PyLauncher.Source @("-3")
}

# A stale Python Launcher or registry entry can point at a removed OneDrive
# installation. Discover Microsoft Store Python packages directly as a robust
# fallback, then validate every candidate before using it.
try {
  $StorePackages = Get-AppxPackage -Name "PythonSoftwareFoundation.Python*" -ErrorAction Stop |
    Sort-Object Version -Descending
  foreach ($package in $StorePackages) {
    $majorMinor = "$($package.Version.Major).$($package.Version.Minor)"
    foreach ($name in @("python$majorMinor.exe", "python.exe", "python3.exe")) {
      $candidatePath = Join-Path $package.InstallLocation $name
      if (Test-Path -LiteralPath $candidatePath) {
        Add-PythonCandidate $PythonCandidates $candidatePath
      }
    }
  }
} catch {
  # Non-Store Python installations are already covered by Get-Command above.
}

$PythonRuntime = $null
foreach ($candidate in $PythonCandidates) {
  if (Test-PythonRuntime $candidate.Executable $candidate.PrefixArgs) {
    $PythonRuntime = $candidate
    break
  }
}

if (-not $PythonRuntime) {
  throw "Python 3.10 or newer is required for the host CDP helper installer. Install Python, then run this script again."
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
if result.get("task_name"):
    print(f"Scheduled task: {result['task_name']}")
else:
    print(f"Startup entry: {result.get('startup_entry', '')}")
    print("Windows denied scheduled-task creation, so the installer used the current-user Startup folder instead.")
print("Docker Compose will read these values from .env:")
for key, value in values.items():
    shown = "<redacted>" if key == "WEB_CDP_HELPER_TOKEN" else value
    print(f"  {key}={shown}")
print("Restart Docker Compose after changing host CDP helper settings.")
'@

$TempScript = Join-Path ([System.IO.Path]::GetTempPath()) ("nanobot-cdp-helper-" + [System.Guid]::NewGuid().ToString("N") + ".py")
Set-Content -Path $TempScript -Value $Code -Encoding UTF8

try {
  $PythonExecutable = $PythonRuntime.Executable
  $PythonPrefixArgs = @($PythonRuntime.PrefixArgs)
  & $PythonExecutable @PythonPrefixArgs $TempScript $RootDir
  $ExitCode = $LASTEXITCODE
  if ($ExitCode -ne 0) {
    exit $ExitCode
  }
} finally {
  Remove-Item -Force $TempScript -ErrorAction SilentlyContinue
}
