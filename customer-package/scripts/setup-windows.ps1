param(
  [switch]$SkipCdpHelper,
  [switch]$PullOnly
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PackageDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $PackageDir

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

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  throw "Docker Desktop is required. Install it and try again."
}
try {
  docker version *> $null
} catch {
  throw "Docker Engine is not ready. Start Docker Desktop and wait until it is running."
}
if ($LASTEXITCODE -ne 0) {
  throw "Docker Engine is not ready. Start Docker Desktop and wait until it is running."
}

Copy-TemplateIfMissing ".env.example" ".env"
Copy-TemplateIfMissing "config.example.json" "config.json"
Copy-TemplateIfMissing "google.example.json" "google.json"
Copy-TemplateIfMissing "supabase.example.json" "supabase.json"

$ConfigText = (Get-Content -Raw -LiteralPath "config.json") + (Get-Content -Raw -LiteralPath "supabase.json")
if ($ConfigText -match "YOUR_[A-Z_]+|CUSTOMER_VIRTUAL_KEY") {
  throw "Customer configuration is incomplete. Ask the package provider for a configured config.json and supabase.json."
}

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
  $Helper = Join-Path $PackageDir "cdp-helper\nanobot-cdp-helper.exe"
  if (-not (Test-Path -LiteralPath $Helper)) {
    throw "The Windows CDP Helper is missing from cdp-helper. Ask the package provider for a complete installer."
  }
  & $Helper install --project-root $PackageDir
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to install the Windows CDP Helper."
  }
}

docker compose -f compose.yml pull
if ($LASTEXITCODE -ne 0) {
  throw "Unable to pull the release images. Check Docker Hub access and CLAW_VERSION in .env."
}

if (-not $PullOnly) {
  docker compose -f compose.yml up -d
  if ($LASTEXITCODE -ne 0) {
    throw "The containers could not be started. Run Logs-Windows.cmd for details."
  }
  Write-Host "Claw Insurance is running at http://localhost:8080"
}
