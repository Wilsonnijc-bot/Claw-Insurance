# Proxy Architecture & NanoBot Configuration Summary

## Purpose

把敏感上游凭据与调用逻辑移到服务器端代理（DB 与 Interview），并让 NanoBot 只需短期/子密钥与代理地址即可工作。

## Files Changed

- `config.json` — 对齐为使用 `providers.litellm`（指向服务器 LiteLLM）、`catalog.db_proxy` 与 `interviewProxy`。
- `config.example.json` — 同步示例位置与占位值。
- `nanobot/insurance_catalog.py` — 新增 `catalog.db_proxy` 读取与 `_fetch_table_rows_via_proxy()`（分页、Bearer 验证、兼容多种返回形态）。
- `nanobot/config/google_loader.py` & `nanobot/providers/google_speech.py` — 当 `interviewProxy` 存在时，走外部识别代理（POST base64 音频，接受多种响应形态）；保留本地 Google JSON 回退。
- `nanobot/config/schema.py` — 添加 `interview_proxy` 字段。
- `server_proxy/` — 新增 `db_proxy` 与 `interview_proxy` FastAPI 服务、Dockerfiles、requirements、`docker-compose.yml` 与 `.env.example`（并修复 LiteLLM 与 Postgres 密码变量一致性）。

## Server-side Responsibilities

### db-proxy

- Endpoint: `POST /query`
- Auth: `Authorization: Bearer <DB_PROXY_API_KEY>`（可选，取决于 env）
- Request JSON: `{ "query_type": "select", "table": "<table>", "limit": n, "offset": m }`
- Internal: 使用 `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` 向 Supabase REST API 请求并返回 `{ "rows": [...] }` 或直接列表
- Errors: 非 200 或网络错误返回 4xx/502，携带 detail 便于调试

### interview-proxy

- Endpoint: `POST /recognize`
- Auth: `Authorization: Bearer <INTERVIEW_PROXY_API_KEY>`
- Request JSON: `{ "audio_base64": "...", "language": "zh-HK" }`
- Internal: 读取 `GOOGLE_CREDENTIAL_JSON_PATH`，构建 Google Speech 客户端，解码 base64，调用 recognize，返回 `{ "transcript": "..." }`
- 回退: 无凭证返回 501；库或调用失败返回 500/502

## NanoBot (Client-side) Responsibilities

### Catalog queries

- 优先使用 `catalog.db_proxy.baseUrl` 与 `catalog.db_proxy.apiKey`（或环境变量覆盖）。
- 若配置存在：向 `POST {baseUrl}/query` 发送分页请求（payload 使用 `query_type=select`），带上 `Authorization: Bearer <apiKey>`（若配置）。
- 支持响应形态：顶层 List 或 `{ "rows": [...] }`。
- 若未配置 db_proxy 则回退到直接使用 `supabase_url` + `supabase_anon_key` 的原实现。

### Transcription

- 若主配置含 `interviewProxy`：将音频 bytes base64 编码并 POST 到 `{proxy_url}/recognize`，带上 Bearer 子密钥（若有），解析并接受多种响应形态。
- 否则使用本地 Google service account（`google.json`）。

## Required server `.env` variables

- `LITELLM_MASTER_KEY`
- `LITELLM_DB_PASSWORD`
- `MOONSHOT_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `DB_PROXY_API_KEY`
- `INTERVIEW_PROXY_API_KEY`
- `GOOGLE_CREDENTIAL_JSON_PATH`

## Quick start & test commands

Start services (in `server_proxy/`):

```bash
docker compose up -d --build
```

View logs:

```bash
docker compose logs -f db-proxy
docker compose logs -f interview-proxy
```

Test db-proxy:

```bash
curl -X POST http://<server-ip>:5000/query \
  -H "Authorization: Bearer <DB_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"query_type":"select","table":"insurance_products","limit":5}'
```

Test interview-proxy:

```bash
curl -X POST http://<server-ip>:5001/recognize \
  -H "Authorization: Bearer <INTERVIEW_PROXY_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"audio_base64":"<BASE64_AUDIO>","language":"yue-Hant-HK"}'
```
