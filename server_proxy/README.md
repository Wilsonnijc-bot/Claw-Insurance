Server-side proxy stack

This folder contains a minimal server-side proxy stack you can deploy on your server.

Services:

- litellm: your existing LiteLLM proxy (as provided)
- db-proxy: FastAPI service that exposes a small set of catalog endpoints and internally calls Supabase with your service key
- interview-proxy: FastAPI service that exposes a `/recognize` endpoint and (optionally) calls Google Speech-to-Text with your service account

Quick start (from this folder):

1. Copy your upstream secrets into `.env` (see `.env.example`).
2. Build & run:

```bash
docker compose up -d
```

Endpoints (examples):

- DB proxy: `POST /query` with JSON {"query_type":"select","table":"insurance_products","limit":100,"offset":0}
- Interview proxy: `POST /recognize` with JSON {"audio_base64":"...","language":"yue-Hant-HK"}

Security: both proxies expect a bearer API key header `Authorization: Bearer <key>` matching env variables. Rotate keys before production.

Files:

- `docker-compose.yml` — compose bringing up litellm, db, db-proxy, interview-proxy
- `db_proxy/` — DB proxy FastAPI app
- `interview_proxy/` — Interview proxy FastAPI app

"
