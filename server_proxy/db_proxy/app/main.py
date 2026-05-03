from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel

app = FastAPI(title="DB Proxy")

logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
LEGACY_DB_PROXY_API_KEY = os.environ.get("DB_PROXY_API_KEY")
LITELLM_BASE_URL = (os.environ.get("LITELLM_BASE_URL") or "http://litellm:4000").rstrip("/")
LITELLM_MASTER_KEY = os.environ.get("LITELLM_MASTER_KEY")
AUDIT_DATABASE_URL = os.environ.get("AUDIT_DATABASE_URL") or os.environ.get("DATABASE_URL")


@dataclass(slots=True)
class AuthContext:
    key_hash: str
    key_prefix: str
    user_id: str | None
    tenant_id: str | None
    metadata: dict[str, Any]
    auth_source: str


class QueryRequest(BaseModel):
    query_type: str
    table: str
    limit: int | None = None
    offset: int | None = None


def _extract_presented_key(request: Request) -> str:
    header = request.headers.get("x-litellm-key") or request.headers.get("authorization") or ""
    header = header.strip()
    if not header:
        return ""
    if header.lower().startswith("bearer "):
        return header.split(None, 1)[1].strip()
    return header


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _key_prefix(key: str) -> str:
    if len(key) <= 8:
        return key
    return f"{key[:4]}...{key[-4:]}"


def _metadata_from_key_info(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    metadata = {}
    if isinstance(info, dict):
        raw_metadata = info.get("metadata")
        if isinstance(raw_metadata, dict):
            metadata = raw_metadata
    return metadata


def _resolve_identity(metadata: dict[str, Any]) -> tuple[str | None, str | None]:
    user_id = metadata.get("user_id") or metadata.get("user")
    tenant_id = metadata.get("tenant_id") or metadata.get("team_id")
    return (str(user_id) if user_id is not None else None, str(tenant_id) if tenant_id is not None else None)


def _has_service_permission(metadata: dict[str, Any], service_name: str) -> bool:
    flag_name = f"can_use_{service_name}"
    if metadata.get(flag_name) is True:
        return True

    services = metadata.get("services")
    if isinstance(services, list) and (service_name in services or "*" in services):
        return True

    permissions = metadata.get("permissions")
    if isinstance(permissions, list):
        allowed_markers = {service_name, f"can_use_{service_name}", "*"}
        if any(str(item) in allowed_markers for item in permissions):
            return True

    scopes = metadata.get("scopes")
    if isinstance(scopes, list):
        allowed_markers = {service_name, f"can_use_{service_name}", "*"}
        if any(str(item) in allowed_markers for item in scopes):
            return True

    return False


def _request_summary(req: QueryRequest) -> dict[str, Any]:
    return {
        "table": req.table,
        "query_type": req.query_type,
        "limit": req.limit,
        "offset": req.offset,
    }


async def _ensure_audit_schema() -> None:
    if not AUDIT_DATABASE_URL:
        return

    def _create_schema_sync() -> None:
        with psycopg.connect(AUDIT_DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS proxy_audit_logs (
                        id BIGSERIAL PRIMARY KEY,
                        request_id TEXT NOT NULL,
                        service_name TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        method TEXT NOT NULL,
                        key_hash TEXT NOT NULL,
                        key_prefix TEXT NOT NULL,
                        user_id TEXT,
                        tenant_id TEXT,
                        allowed BOOLEAN NOT NULL,
                        status_code INTEGER NOT NULL,
                        latency_ms INTEGER NOT NULL,
                        client_ip TEXT,
                        details JSONB NOT NULL DEFAULT '{}'::jsonb,
                        error_message TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_proxy_audit_logs_created_at ON proxy_audit_logs (created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_proxy_audit_logs_key_hash ON proxy_audit_logs (key_hash, created_at DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_proxy_audit_logs_service_name ON proxy_audit_logs (service_name, created_at DESC)"
                )

    await asyncio.to_thread(_create_schema_sync)


async def _write_audit_log(
    *,
    request_id: str,
    service_name: str,
    endpoint: str,
    method: str,
    key_hash: str,
    key_prefix: str,
    user_id: str | None,
    tenant_id: str | None,
    allowed: bool,
    status_code: int,
    latency_ms: int,
    client_ip: str | None,
    details: dict[str, Any],
    error_message: str | None = None,
) -> None:
    if not AUDIT_DATABASE_URL:
        return

    def _write_sync() -> None:
        with psycopg.connect(AUDIT_DATABASE_URL, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO proxy_audit_logs (
                        request_id, service_name, endpoint, method,
                        key_hash, key_prefix, user_id, tenant_id,
                        allowed, status_code, latency_ms, client_ip,
                        details, error_message
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s
                    )
                    """,
                    (
                        request_id,
                        service_name,
                        endpoint,
                        method,
                        key_hash,
                        key_prefix,
                        user_id,
                        tenant_id,
                        allowed,
                        status_code,
                        latency_ms,
                        client_ip,
                        Jsonb(details),
                        error_message,
                    ),
                )

    try:
        await asyncio.to_thread(_write_sync)
    except Exception:
        logger.exception("Failed to write audit log")


async def _fetch_litellm_key_info(key: str) -> dict[str, Any]:
    if not (LITELLM_BASE_URL and LITELLM_MASTER_KEY):
        return {}

    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
        "Accept": "application/json",
    }
    url = f"{LITELLM_BASE_URL}/key/info"

    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, params={"key": key}, headers=headers)
        if response.status_code in {404, 405}:
            response = await client.post(url, json={"key": key}, headers=headers)

    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"LiteLLM key lookup failed ({response.status_code}): {response.text[:300]}")

    try:
        body = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LiteLLM key lookup returned invalid JSON: {exc}") from exc

    if not isinstance(body, dict):
        raise HTTPException(status_code=502, detail="LiteLLM key lookup returned unexpected payload")
    return body


async def _authorize_request(request: Request, service_name: str) -> tuple[str, AuthContext]:
    key = _extract_presented_key(request)
    if not key:
        raise HTTPException(status_code=401, detail="Missing API key")

    if LITELLM_BASE_URL and LITELLM_MASTER_KEY:
        key_info = await _fetch_litellm_key_info(key)
        metadata = _metadata_from_key_info(key_info)
        if not _has_service_permission(metadata, service_name):
            raise HTTPException(status_code=403, detail=f"Key is not allowed to use {service_name}")

        user_id, tenant_id = _resolve_identity(metadata)
        return key, AuthContext(
            key_hash=_key_hash(key),
            key_prefix=_key_prefix(key),
            user_id=user_id,
            tenant_id=tenant_id,
            metadata=metadata,
            auth_source="litellm",
        )

    if LEGACY_DB_PROXY_API_KEY and key != LEGACY_DB_PROXY_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return key, AuthContext(
        key_hash=_key_hash(key),
        key_prefix=_key_prefix(key),
        user_id=None,
        tenant_id=None,
        metadata={},
        auth_source="legacy",
    )

@app.post("/query")
async def query(req: QueryRequest, request: Request):
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    client_ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)

    try:
        _, auth_context = await _authorize_request(request, "db")
    except HTTPException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        presented_key = _extract_presented_key(request)
        await _write_audit_log(
            request_id=request_id,
            service_name="db",
            endpoint=str(request.url.path),
            method=request.method,
            key_hash=_key_hash(presented_key) if presented_key else "",
            key_prefix=_key_prefix(presented_key) if presented_key else "",
            user_id=None,
            tenant_id=None,
            allowed=False,
            status_code=exc.status_code,
            latency_ms=elapsed_ms,
            client_ip=client_ip,
            details=_request_summary(req),
            error_message=str(exc.detail),
        )
        raise

    if SUPABASE_URL is None or SUPABASE_SERVICE_KEY is None:
        raise HTTPException(status_code=500, detail="Supabase not configured on server")

    if req.query_type != "select":
        raise HTTPException(status_code=400, detail="Only 'select' query_type is supported")

    table_name = req.table.strip()
    url = SUPABASE_URL.rstrip("/") + "/rest/v1/" + table_name
    params = {"select": "*"}
    if req.limit:
        params["limit"] = req.limit
    if req.offset:
        params["offset"] = req.offset

    if auth_context.user_id:
        params["user_id"] = f"eq.{auth_context.user_id}"
    elif auth_context.tenant_id:
        params["tenant_id"] = f"eq.{auth_context.tenant_id}"

    headers = {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Accept": "application/json",
    }

    query_details = _request_summary(req)
    query_details.update(
        {
            "auth_source": auth_context.auth_source,
            "user_id": auth_context.user_id,
            "tenant_id": auth_context.tenant_id,
        }
    )

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, params=params, headers=headers)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await _write_audit_log(
            request_id=request_id,
            service_name="db",
            endpoint=str(request.url.path),
            method=request.method,
            key_hash=auth_context.key_hash,
            key_prefix=auth_context.key_prefix,
            user_id=auth_context.user_id,
            tenant_id=auth_context.tenant_id,
            allowed=True,
            status_code=502,
            latency_ms=elapsed_ms,
            client_ip=client_ip,
            details=query_details,
            error_message=str(exc),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if r.status_code >= 400:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await _write_audit_log(
            request_id=request_id,
            service_name="db",
            endpoint=str(request.url.path),
            method=request.method,
            key_hash=auth_context.key_hash,
            key_prefix=auth_context.key_prefix,
            user_id=auth_context.user_id,
            tenant_id=auth_context.tenant_id,
            allowed=True,
            status_code=502,
            latency_ms=elapsed_ms,
            client_ip=client_ip,
            details=query_details,
            error_message=r.text[:300],
        )
        raise HTTPException(status_code=502, detail=r.text[:300])

    try:
        data = r.json()
    except Exception:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await _write_audit_log(
            request_id=request_id,
            service_name="db",
            endpoint=str(request.url.path),
            method=request.method,
            key_hash=auth_context.key_hash,
            key_prefix=auth_context.key_prefix,
            user_id=auth_context.user_id,
            tenant_id=auth_context.tenant_id,
            allowed=True,
            status_code=502,
            latency_ms=elapsed_ms,
            client_ip=client_ip,
            details=query_details,
            error_message="Invalid JSON from Supabase",
        )
        raise HTTPException(status_code=502, detail="Invalid JSON from Supabase")

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await _write_audit_log(
        request_id=request_id,
        service_name="db",
        endpoint=str(request.url.path),
        method=request.method,
        key_hash=auth_context.key_hash,
        key_prefix=auth_context.key_prefix,
        user_id=auth_context.user_id,
        tenant_id=auth_context.tenant_id,
        allowed=True,
        status_code=200,
        latency_ms=elapsed_ms,
        client_ip=client_ip,
        details={**query_details, "row_count": len(data) if isinstance(data, list) else None},
    )

    return {"rows": data}


@app.on_event("startup")
async def _startup() -> None:
    await _ensure_audit_schema()
