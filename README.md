## Project Overview

Insurance agents spend a lot of time answering repeated client questions on WhatsApp and WeChat. This project is an AI assistant that helps agents draft accurate replies faster by using structured insurance product knowledge.

The assistant can:

- draft replies for the latest client message
- generate auto-drafts for selected reply targets
- ground insurance recommendations in a configured product catalog
- extract and store client conversation context
- sync WhatsApp conversation history through a host Chrome/Chromium helper
- transcribe offline meeting notes when Google Speech-to-Text or the interview proxy is configured
- route catalog and speech services through optional server-side proxies for safer key management and audit logging

## Outcome / Impact

- 20+ paying users
- users from insurance firms including Prudential and AIA
- 2+ hours saved per user per day
- real revenue generated
- product iterations driven by real user feedback
- Docker-based deployment for repeatable local setup

## Quick Start with Docker

Docker Compose is the supported runtime for this project.

### 1. Clone the repository

```bash
git clone https://github.com/Wilsonnijc-bot/Claw-Insurance.git
cd Claw-Insurance
```

### 2. Install host prerequisites

Install these on the host machine:

- Docker Desktop or another Docker engine with Compose v2
- Chrome or Chromium if WhatsApp history sync is needed

WhatsApp history sync uses a host-side Chrome/Chromium CDP helper. From the project root, run the installer for your host platform only if history sync is needed:

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

The installer verifies host Chrome/Chromium, configures and starts the host CDP helper, and writes Docker-facing values to `.env`:

- `WEB_CDP_URL`
- `WEB_CDP_HELPER_URL`
- `WEB_CDP_HELPER_TOKEN`
- `WEB_CDP_HELPER_PLATFORM`
- `WEB_HOST_PROFILE_DIR`
- `WEB_HISTORY_SYNC_ENABLED`

Default helper URLs:

- `WEB_CDP_URL=http://host.docker.internal:9222`
- `WEB_CDP_HELPER_URL=http://host.docker.internal:9230`

After changing host CDP helper settings, restart the Docker stack.

### 3. Create local configuration files

```bash
cp config.example.json config.json
cp google.example.json google.json
cp supabase.example.json supabase.json
```

Edit `config.json` with the LiteLLM API key, model, and proxy settings you need.

Keep `google.json` only if you use Google Speech-to-Text. If Google Speech-to-Text is enabled, place the credential JSON under `secrets/` and point `google.json` at that file.

Keep `supabase.json` only if you use Supabase-backed catalog features.

### 4. Build and run the app

```bash
docker compose up -d --build
```

Open the frontend:

```text
http://localhost:8080
```

The backend API runs on:

```text
http://localhost:3456
```

Log in from the frontend and complete the WhatsApp login / QR flow if needed.

### 5. Common Docker commands

Run these from the project root:

```bash
docker compose up -d
docker compose down
docker compose ps
docker compose logs -f
docker compose logs -f nanobot-gateway
docker compose restart nanobot-gateway
```

`docker compose down` stops the stack without deleting project files in this repository.

## Key Features

- RAG-powered insurance reply generation using configured catalog data
- AI draft generation for the latest client message
- Auto-draft support for selected WhatsApp reply targets
- Client list, message thread, and reply composer in a React web UI
- WhatsApp login and history sync support
- Offline meeting note transcription and storage
- Server-side proxy option for Supabase catalog access and Google Speech-to-Text
- Audit logging for proxy requests
- Docker Compose deployment

## Tech Stack

### Frontend

- React 18
- TypeScript
- Vite
- Tailwind CSS
- Lucide React icons
- Nginx container for serving the built frontend

### Backend

- Python 3.12
- Nanobot assistant framework
- Typer
- Pydantic and pydantic-settings
- HTTPX, aiohttp, and websockets
- python-socketio
- WhatsApp bridge built with Node.js 20

### AI / LLM

- LiteLLM provider integration
- OpenAI Python SDK dependency
- Insurance product advisor skill
- Catalog-grounded product matching workflow
- Optional Tavily-based brochure research through the local skill workflow
- Google Cloud Speech-to-Text for transcription

### Database / Storage

- Project-local runtime files under directories such as `data/`, `state/`, `memory/`, and `sessions/`
- Supabase-backed insurance catalog support
- Optional PostgreSQL audit database for the server proxy stack

### Deployment

- Docker
- Docker Compose
- Backend container built from the root `Dockerfile`
- Frontend container built from `Insurance frontend/Dockerfile`
- Optional server proxy Docker Compose stack under `server_proxy/`

## Services

The main `docker-compose.yml` starts:

- `nanobot-gateway`: backend launcher/API on `http://localhost:3456`
- `nanobot-frontend`: web UI on `http://localhost:8080`

## Optional Server Proxy Architecture

This project includes optional server-side proxy services for unified key management and audit logging. Sensitive upstream credentials such as Supabase and Google Speech-to-Text can stay on the server, while Nanobot uses proxy URLs and virtual keys.

For detailed architecture, see [server_proxy/PROXY_SUMMARY.md](server_proxy/PROXY_SUMMARY.md).

### Proxy Components

- `db-proxy` (`server_proxy/db_proxy`): queries Supabase via `POST /query` with LiteLLM key validation
- `interview-proxy` (`server_proxy/interview_proxy`): speech-to-text via `POST /recognize` with LiteLLM key validation
- LiteLLM key management: validates requests against virtual keys with `user_id`, `tenant_id`, `can_use_db`, and `can_use_interview` metadata
- PostgreSQL audit logging: records each proxy request with request ID, service name, key hash, user ID, tenant ID, status code, and latency

### Nanobot Proxy Configuration

Proxy endpoints are configured in `config.json`:

```json
{
  "catalog": {
    "db_proxy": {
      "baseUrl": "http://server-ip:5000",
      "apiKey": "<DB_PROXY_API_KEY>"
    }
  },
  "interviewProxy": {
    "baseUrl": "http://server-ip:5001",
    "apiKey": "<INTERVIEW_PROXY_API_KEY>"
  },
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

See [server_proxy/README.md](server_proxy/README.md) and [server_proxy/PROXY_SUMMARY.md](server_proxy/PROXY_SUMMARY.md) for proxy-specific configuration.

### Proxy Smoke Tests

After the server proxy stack is running, check the database proxy:

```bash
curl -X POST http://localhost:5000/query \
  -H "Authorization: Bearer <DB_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"select","table":"insurance_products","limit":5}'
```

Check the interview proxy:

```bash
curl -X POST http://localhost:5001/recognize \
  -H "Authorization: Bearer <INTERVIEW_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"audio_base64":"<BASE64_AUDIO>","language":"yue-Hant-HK"}'
```

Query recent audit logs:

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
