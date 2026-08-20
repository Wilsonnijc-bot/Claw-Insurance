param(
  [switch]$SkipCdpHelper,
  [switch]$PullOnly
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RootDir

function Require-Command {
  param([Parameter(Mandatory = $true)][string]$Name)
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
    throw "$Name is required. Install Docker Desktop and try again."
  }
}

function Copy-TemplateIfMissing {
  param(
    [Parameter(Mandatory = $true)][string]$Template,
    [Parameter(Mandatory = $true)][string]$Destination
  )
  if (-not (Test-Path -LiteralPath $Destination)) {
    Copy-Item -LiteralPath $Template -Destination $Destination
    Write-Host "Created $Destination"
  }
}

Require-Command docker
docker version *> $null
if ($LASTEXITCODE -ne 0) {
  throw "Docker Engine is not ready. Start Docker Desktop and wait for it to finish starting."
}

Copy-TemplateIfMissing ".env.example" ".env"
Copy-TemplateIfMissing "config.example.json" "config.json"
Copy-TemplateIfMissing "google.example.json" "google.json"
Copy-TemplateIfMissing "supabase.example.json" "supabase.json"

@(
  "secrets",
  "runtime/data",
  "runtime/sessions",
  "runtime/state",
  "runtime/memory",
  "runtime/media",
  "runtime/cron",
  "runtime/skills",
  "whatsapp-auth",
  "whatsapp-web",
  "whatsapp-web-debug"
) | ForEach-Object {
  New-Item -ItemType Directory -Force -Path $_ | Out-Null
}

if (-not $SkipCdpHelper) {
  $BundledHelper = Join-Path $RootDir "cdp-helper\nanobot-cdp-helper.exe"
  if (Test-Path -LiteralPath $BundledHelper) {
    & $BundledHelper install --project-root $RootDir
  } else {
    & (Join-Path $ScriptDir "install-cdp-helper-windows.ps1")
  }
}

docker compose -f compose.release.yml pull
if ($LASTEXITCODE -ne 0) {
  throw "Unable to pull the release images. Check Docker Hub access and the version in .env."
}

if (-not $PullOnly) {
  docker compose -f compose.release.yml up -d
  if ($LASTEXITCODE -ne 0) {
    throw "The containers could not be started. Run docker compose -f compose.release.yml logs."
  }
  Write-Host "Claw Insurance is running at http://localhost:8080"
}
