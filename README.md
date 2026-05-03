# Nanobot WhatsApp

Local WhatsApp operator workspace with:

- a backend on `3456`
- a frontend on `8080` in Docker
- project-local runtime data in this repo

## Stop The Current Docker Stack

If you want to stop the containers started from this repo, run this in the project root:

```bash
docker compose down
```

That is the exact command to stop the current Docker setup.

## Docker Setup

Docker is the recommended way to run this project.

### Prerequisites

- Docker Desktop or another Docker engine with Compose v2
- macOS / Linux: `python3` must be available
- Windows: `py -3` must be available
- Chrome/Chromium is required on the host for Docker WhatsApp history sync

### 1. Clone The Repo

```bash
git clone https://github.com/Wilsonnijc-bot/Claw-Insurance.git
cd Claw-Insurance
```

### 2. Create Config Files

```bash
cp config.example.json config.json
cp google.example.json google.json
cp supabase.example.json supabase.json
```

Then:

- edit `config.json` and add the real API key / model settings you need
- keep `google.json` only if you use Google STT
- keep `supabase.json` only if you use Supabase features
- if you use Google STT, put the real credential JSON inside `secrets/`

### 3. Build And Start

```bash
python3 -m nanobot docker-up
```

That command does the host checks, installs or reuses the host CDP helper, and then runs:

```bash
docker compose up -d --build
```

Platform commands:

- macOS: `./docker-up` or `python3 -m nanobot docker-up`
- Linux / Huawei Linux: `python3 -m nanobot docker-up`
- Windows / Huawei Windows: `py -3 -m nanobot docker-up`

### 4. Open The App

Open:

```text
http://localhost:8080
```

Then log in and complete WhatsApp login / QR steps if needed.

## Daily Docker Use

Use these commands from the project root.

```bash
python3 -m nanobot docker-up       # macOS/Linux build and start everything
py -3 -m nanobot docker-up         # Windows build and start everything
./docker-up                        # macOS compatibility wrapper
docker compose up -d               # start without rebuilding
docker compose ps                  # show running services
docker compose logs -f             # follow all logs
docker compose logs -f nanobot-gateway
docker compose restart nanobot-gateway
docker compose down                # stop everything
```

Useful notes:

- macOS: use `./docker-up` or `python3 -m nanobot docker-up`
- Linux / Huawei Linux: use `python3 -m nanobot docker-up`
- Windows / Huawei Windows: use `py -3 -m nanobot docker-up`
- `docker compose down` stops the current stack
- `docker compose down` does not delete your project files in this repo

## Server-side Proxy Architecture & Unified Key Management

This project implements **unified key management and audit logging** through server-side proxy services. Sensitive upstream credentials (Supabase, Google Speech) remain on the server, while NanoBot only needs short-lived virtual keys and proxy addresses.

### Architecture Overview

- **db-proxy** (`server_proxy/db_proxy`): Queries Supabase via `POST /query` with LiteLLM key validation
- **interview-proxy** (`server_proxy/interview_proxy`): Speech-to-text via `POST /recognize` with LiteLLM key validation
- **LiteLLM Key Management**: All requests validated against LiteLLM virtual keys (with `user_id`, `tenant_id`, `can_use_db`, `can_use_interview` metadata)
- **PostgreSQL Audit Logging**: Every proxy request logged to `proxy_audit_logs` table (request_id, service_name, key_hash, user_id, tenant_id, status_code, latency_ms, etc.)

For detailed architecture, see [server_proxy/PROXY_SUMMARY.md](server_proxy/PROXY_SUMMARY.md).

### Configuration (config.json)

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

### Required Server .env Variables

Set these in `server_proxy/.env` before starting:

```bash
# LiteLLM
LITELLM_MASTER_KEY=<admin-key-for-key-generation>
LITELLM_DB_PASSWORD=<password>

# Upstream credentials (server-side only)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_KEY=<service-role-key>
GOOGLE_CREDENTIAL_JSON_PATH=/app/credentials/google.json

# Proxy API keys (shared with NanoBot in config.json)
DB_PROXY_API_KEY=<random-key-for-db-access>
INTERVIEW_PROXY_API_KEY=<random-key-for-speech>

# Audit database
AUDIT_DATABASE_URL=postgresql://user:password@postgres:5432/audit_db
```

### Quick Test Commands

After `docker compose up -d --build`:

**Test db-proxy:**

```bash
curl -X POST http://localhost:5000/query \
  -H "Authorization: Bearer <DB_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"select","table":"insurance_products","limit":5}'
```

**Test interview-proxy:**

```bash
curl -X POST http://localhost:5001/recognize \
  -H "Authorization: Bearer <INTERVIEW_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"audio_base64":"<BASE64_AUDIO>","language":"yue-Hant-HK"}'
```

**View audit logs (from PostgreSQL):**

```bash
# Connect to audit database
psql $AUDIT_DATABASE_URL -c "SELECT service_name, user_id, tenant_id, status_code, latency_ms FROM proxy_audit_logs ORDER BY created_at DESC LIMIT 10;"
```

### Operator Workflow

1. **Generate LiteLLM virtual keys** with desired metadata (user_id, tenant_id, permissions):
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
2. Share the generated key with the NanoBot user (for `config.json`)
3. Monitor audit logs to verify all requests

### User Workflow

1. Fill `config.json` with proxy URLs and API keys from your operator
2. Start NanoBot: `python3 -m nanobot docker-up` (macOS/Linux) or `py -3 -m nanobot docker-up` (Windows)
3. All database queries and speech recognition are routed through proxies with automatic audit logging

For complete details, see [server_proxy/PROXY_SUMMARY.md](server_proxy/PROXY_SUMMARY.md).

## Services

- `nanobot-gateway`: backend API on `http://localhost:3456`
- `nanobot-frontend`: web UI on `http://localhost:8080`
- `nanobot-cli`: optional CLI profile
- `db-proxy`: database proxy on `http://localhost:5000` (when running in server_proxy/)
- `interview-proxy`: speech recognition proxy on `http://localhost:5001` (when running in server_proxy/)

## Python Commands

If you do not want Docker, these are the main local Python commands:

```bash
./bootstrap
source .venv/bin/activate
python -m nanobot setup
python -m nanobot status
whatsapp-web-nanobot-ui
python -m nanobot stop-dev
```

Keep this mental model:

- Docker: recommended for normal use
- Python commands: fallback for local development or manual setup
