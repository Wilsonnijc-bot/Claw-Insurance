param([string]$OutputDir = "dist/cdp-helper")

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RootDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv is required to build the standalone CDP helper."
}

uv tool run --python 3.12 --from pyinstaller pyinstaller `
  --clean `
  --noconfirm `
  --onefile `
  --name nanobot-cdp-helper `
  --paths $RootDir `
  --workpath "dist/cdp-helper-build" `
  --specpath "dist/cdp-helper-build" `
  --distpath $OutputDir `
  scripts/cdp-helper-entrypoint.py
