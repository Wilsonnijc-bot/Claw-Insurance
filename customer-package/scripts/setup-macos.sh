#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PACKAGE_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
cd "$PACKAGE_DIR"

SKIP_CDP_HELPER="${SKIP_CDP_HELPER:-false}"
PULL_ONLY="${PULL_ONLY:-false}"

command -v docker >/dev/null 2>&1 || {
  echo "Docker Desktop is required." >&2
  exit 1
}
docker version >/dev/null

[ -f .env ] || cp .env.example .env
[ -f config.json ] || cp config.example.json config.json
[ -f google.json ] || cp google.example.json google.json
[ -f supabase.json ] || cp supabase.example.json supabase.json

if grep -Eq 'YOUR_[A-Z_]+|CUSTOMER_VIRTUAL_KEY' config.json supabase.json; then
  echo "Customer configuration is incomplete. Ask the package provider for configured JSON files." >&2
  exit 1
fi

mkdir -p secrets runtime/{data,sessions,state,memory,media,cron,skills} \
  whatsapp-auth whatsapp-web whatsapp-web-debug

if [ "$SKIP_CDP_HELPER" != "true" ]; then
  HELPER="$PACKAGE_DIR/cdp-helper/nanobot-cdp-helper"
  [ -x "$HELPER" ] || {
    echo "The macOS CDP Helper is missing or not executable." >&2
    exit 1
  }
  "$HELPER" install --project-root "$PACKAGE_DIR"
fi

docker compose -f compose.yml pull
if [ "$PULL_ONLY" != "true" ]; then
  docker compose -f compose.yml up -d
  echo "Claw Insurance is running at http://localhost:8080"
fi

