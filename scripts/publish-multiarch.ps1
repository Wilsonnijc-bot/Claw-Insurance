param(
  [string]$Version = "",
  [string]$BackendRepository = "hendrickyan/claw-insurance-backend",
  [string]$FrontendRepository = "hendrickyan/claw-insurance-frontend",
  [string]$Builder = "claw-multiarch",
  [switch]$SkipLatest
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RootDir

if (-not $Version) {
  $Version = (Get-Content -Raw -LiteralPath "VERSION").Trim()
}
if ($Version -notmatch '^\d+\.\d+\.\d+([.-][0-9A-Za-z.-]+)?$') {
  throw "VERSION must be a semantic version such as 1.0.0. Received: $Version"
}

$EnvExample = Get-Content -Raw -LiteralPath ".env.example"
$VersionPattern = "(?m)^CLAW_VERSION=$([regex]::Escape($Version))\s*$"
if ($EnvExample -notmatch $VersionPattern) {
  throw ".env.example must contain CLAW_VERSION=$Version before publishing."
}

if (git status --porcelain) {
  throw "The Git worktree is not clean. Commit or stash the release changes before publishing."
}

docker version *> $null
if ($LASTEXITCODE -ne 0) {
  throw "Docker Engine is not ready."
}

docker buildx inspect $Builder *> $null
if ($LASTEXITCODE -ne 0) {
  docker buildx create --name $Builder --driver docker-container --use
} else {
  docker buildx use $Builder
}
docker buildx inspect --bootstrap

$BackendTags = @("-t", "${BackendRepository}:v${Version}")
$FrontendTags = @("-t", "${FrontendRepository}:v${Version}")
if (-not $SkipLatest) {
  $BackendTags += @("-t", "${BackendRepository}:latest")
  $FrontendTags += @("-t", "${FrontendRepository}:latest")
}

docker buildx build --builder $Builder --platform linux/amd64,linux/arm64 --pull `
  -f Dockerfile @BackendTags --push .
if ($LASTEXITCODE -ne 0) {
  throw "Backend multi-platform build failed."
}

docker buildx build --builder $Builder --platform linux/amd64,linux/arm64 --pull `
  -f frontend/Dockerfile @FrontendTags --push frontend
if ($LASTEXITCODE -ne 0) {
  throw "Frontend multi-platform build failed."
}

docker buildx imagetools inspect "${BackendRepository}:v${Version}"
docker buildx imagetools inspect "${FrontendRepository}:v${Version}"
