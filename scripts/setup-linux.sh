#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
cd "$ROOT_DIR"

SKIP_CDP_HELPER="${SKIP_CDP_HELPER:-false}"
PULL_ONLY="${PULL_ONLY:-false}"

command -v docker >/dev/null 2>&1 || {
  echo "Docker Engine with Compose is required." >&2
  exit 1
}
docker version >/dev/null

[ -f .env ] || cp .env.example .env
[ -f config.json ] || cp config.example.json config.json
[ -f google.json ] || cp google.example.json google.json
[ -f supabase.json ] || cp supabase.example.json supabase.json

mkdir -p secrets runtime/{data,sessions,state,memory,media,cron,skills} \
  whatsapp-auth whatsapp-web whatsapp-web-debug

if [ "$SKIP_CDP_HELPER" != "true" ]; then
  if [ -x "$ROOT_DIR/cdp-helper/nanobot-cdp-helper" ]; then
    "$ROOT_DIR/cdp-helper/nanobot-cdp-helper" install --project-root "$ROOT_DIR"
  else
    "$SCRIPT_DIR/install-cdp-helper-linux.sh"
  fi
fi

docker compose -f compose.release.yml pull
if [ "$PULL_ONLY" != "true" ]; then
  docker compose -f compose.release.yml up -d
  echo "Claw Insurance is running at http://localhost:8080"
fi
