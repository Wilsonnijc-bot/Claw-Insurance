# Nanobot WhatsApp

Docker-only local WhatsApp operator workspace with:

- backend API on `http://localhost:3456`
- frontend on `http://localhost:8080`
- project-local runtime data in this repository

Docker Compose remains the only supported app runtime.

## One-Time Host Prerequisites

Install these on the host machine before running the app:

- Docker Desktop or another Docker engine with Compose v2
- Chrome/Chromium on the host when WhatsApp history sync is needed

Docker does not install host services. WhatsApp history sync uses a host-side Chrome/Chromium CDP helper, so install the helper once on the host only if history sync is needed.

From the project root, run the installer for your host platform:

```bash
scripts/install-cdp-helper-macos.sh
```

```bash
scripts/install-cdp-helper-linux.sh
```

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\scripts\install-cdp-helper-windows.ps1
```

The installer verifies host Chrome/Chromium, configures and starts the host CDP helper, and writes the Docker-facing values to `.env`:

- `WEB_CDP_URL`
- `WEB_CDP_HELPER_URL`
- `WEB_CDP_HELPER_TOKEN`
- `WEB_CDP_HELPER_PLATFORM`
- `WEB_HOST_PROFILE_DIR`
- `WEB_HISTORY_SYNC_ENABLED`

The default Docker-facing helper URLs are:

- `WEB_CDP_URL=http://host.docker.internal:9222`
- `WEB_CDP_HELPER_URL=http://host.docker.internal:9230`

After changing host CDP helper settings, restart the Docker stack.

## Setup

Clone the repository:

```bash
git clone https://github.com/Wilsonnijc-bot/Claw-Insurance.git
cd Claw-Insurance
```

Create local config files:

```bash
cp config.example.json config.json
cp google.example.json google.json
cp supabase.example.json supabase.json
```

Edit `config.json` with the API key and model settings you need. Keep `google.json` only if you use Google STT, and keep `supabase.json` only if you use Supabase-backed catalog features.

If Google STT is enabled, place the credential JSON under `secrets/` and point `google.json` at that file.

## Daily Docker Runtime

Build and start the full app from the project root:

```bash
docker compose up -d --build
```

Open:

```text
http://localhost:8080
```

Log in from the frontend and complete the WhatsApp login / QR flow if needed.

Use these commands from the project root:

```bash
docker compose up -d
docker compose down
docker compose ps
docker compose logs -f
docker compose logs -f nanobot-gateway
docker compose restart nanobot-gateway
```

`docker compose down` stops the stack without deleting project files in this repository.

## Services

- `nanobot-gateway`: backend launcher/API on `http://localhost:3456`
- `nanobot-frontend`: web UI on `http://localhost:8080`

## Server-Side Proxy Architecture & Unified Key Management

This project implements unified key management and audit logging through server-side proxy services. Sensitive upstream credentials such as Supabase and Google Speech stay on the server, while Nanobot only needs short-lived virtual keys and proxy addresses.

### Architecture Overview

- `db-proxy` (`server_proxy/db_proxy`): queries Supabase via `POST /query` with LiteLLM key validation
- `interview-proxy` (`server_proxy/interview_proxy`): speech-to-text via `POST /recognize` with LiteLLM key validation
- LiteLLM key management: validates requests against virtual keys with `user_id`, `tenant_id`, `can_use_db`, and `can_use_interview` metadata
- PostgreSQL audit logging: records each proxy request in `proxy_audit_logs` with request ID, service name, key hash, user ID, tenant ID, status code, and latency

For detailed architecture, see [server_proxy/PROXY_SUMMARY.md](server_proxy/PROXY_SUMMARY.md).

### Nanobot Proxy Configuration

The proxy endpoints are configured in `config.json`:

```json
{
  "catalog": {
    "db_proxy": {
      "baseUrl": "http://server-ip:5000",
      "apiKey": "<DB_PROXY_API_KEY>"
    }
  },
  "interviewProxy": "http://server-ip:5001",
  "providers": {
    "litellm": {
      "baseUrl": "http://server-ip:4000",
      "apiKey": "<LITELLM_VIRTUAL_KEY>"
    }
  }
}
```

### Required Server Environment

Set these in `server_proxy/.env` before starting the proxy stack:

```bash
LITELLM_MASTER_KEY=<admin-key-for-key-generation>
LITELLM_DB_PASSWORD=<password>

SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
GOOGLE_CREDENTIAL_JSON_PATH=/app/credentials/google.json

DB_PROXY_API_KEY=<random-key-for-db-access>
INTERVIEW_PROXY_API_KEY=<random-key-for-speech>

AUDIT_DATABASE_URL=postgresql://user:password@postgres:5432/audit_db
```

### Proxy Stack Runtime

The optional server proxy stack lives under `server_proxy/` and keeps its own Docker Compose workflow. It provides:

- `litellm` on `4000`
- `db-proxy` on `5000`
- `interview-proxy` on `5001`

From `server_proxy/`, start it with:

```bash
docker compose up -d
```

See `server_proxy/README.md` and `server_proxy/PROXY_SUMMARY.md` for proxy-specific configuration.

### Proxy Smoke Tests

After the server proxy stack is running, the proxy endpoints can be checked with:

```bash
curl -X POST http://localhost:5000/query \
  -H "Authorization: Bearer <DB_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"select","table":"insurance_products","limit":5}'
```

```bash
curl -X POST http://localhost:5001/recognize \
  -H "Authorization: Bearer <INTERVIEW_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"audio_base64":"<BASE64_AUDIO>","language":"yue-Hant-HK"}'
```

Audit logs can be queried from the audit database:

```bash
psql $AUDIT_DATABASE_URL -c "SELECT service_name, user_id, tenant_id, status_code, latency_ms FROM proxy_audit_logs ORDER BY created_at DESC LIMIT 10;"
```

### Operator Workflow

1. Generate LiteLLM virtual keys with the desired metadata: `user_id`, `tenant_id`, `can_use_db`, and `can_use_interview`.
2. Share the generated key and proxy URLs with the Nanobot user for `config.json`.
3. Monitor audit logs to verify proxy requests.

Example key generation request:

```bash
curl -X POST http://litellm:4000/key/generate \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "key_name": "user-123-db",
    "metadata": {
      "user_id": "123",
      "tenant_id": "org-456",
      "can_use_db": true,
      "can_use_interview": false
    }
  }'
```

### User Workflow

1. Fill `config.json` with proxy URLs and API keys from your operator.
2. Start Nanobot with the Docker runtime commands in this README.
3. Database queries and speech recognition are routed through the server-side proxies with audit logging.
