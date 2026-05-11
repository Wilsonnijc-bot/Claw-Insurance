from __future__ import annotations

import asyncio
import base64
import hashlib
import json
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

app = FastAPI(title="Interview Proxy")

logger = logging.getLogger(__name__)

INTERVIEW_PROXY_API_KEY = os.environ.get("INTERVIEW_PROXY_API_KEY")
GOOGLE_CREDENTIAL_JSON_PATH = os.environ.get("GOOGLE_CREDENTIAL_JSON_PATH")
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

class RecognizeRequest(BaseModel):
    audio_base64: str
    language: str | None = None


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

    if INTERVIEW_PROXY_API_KEY and key != INTERVIEW_PROXY_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    return key, AuthContext(
        key_hash=_key_hash(key),
        key_prefix=_key_prefix(key),
        user_id=None,
        tenant_id=None,
        metadata={},
        auth_source="legacy",
    )


def _request_summary(req: RecognizeRequest) -> dict[str, Any]:
    return {
        "language": req.language,
        "audio_bytes": len(req.audio_base64 or ""),
    }


@app.post("/recognize")
async def recognize(req: RecognizeRequest, request: Request):
    request_id = uuid.uuid4().hex
    started = time.perf_counter()
    client_ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)

    try:
        _, auth_context = await _authorize_request(request, "interview")
    except HTTPException as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        presented_key = _extract_presented_key(request)
        await _write_audit_log(
            request_id=request_id,
            service_name="interview",
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

    # If google credentials are available, attempt to call Google Speech-to-Text
    if not GOOGLE_CREDENTIAL_JSON_PATH or not os.path.exists(GOOGLE_CREDENTIAL_JSON_PATH):
        raise HTTPException(status_code=501, detail="Google credentials not configured on server")

    try:
        from google.oauth2 import service_account
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Missing google libraries: {exc}")

    # Build client
    try:
        creds = None
        with open(GOOGLE_CREDENTIAL_JSON_PATH, "r", encoding="utf-8") as fh:
            import json
            payload = json.load(fh)
            creds = service_account.Credentials.from_service_account_info(payload)
        client = SpeechClient(credentials=creds)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to initialize Google client: {exc}")

    # Decode audio — expecting raw audio bytes (user should send proper format)
    try:
        audio_bytes = base64.b64decode(req.audio_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="audio_base64 is not valid base64")

    # Build request using auto-detect decoding
    config = cloud_speech.RecognitionConfig(
        auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
        language_codes=[req.language or "en-US"],
        model="chirp_3",
    )
    request_proto = cloud_speech.RecognizeRequest(
        recognizer=f"projects/{payload.get('project_id')}/locations/global/recognizers/_",
        config=config,
        content=audio_bytes,
    )

    try:
        response = client.recognize(request=request_proto, timeout=60.0)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        await _write_audit_log(
            request_id=request_id,
            service_name="interview",
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
            details=_request_summary(req),
            error_message=f"Google STT request failed: {exc}",
        )
        raise HTTPException(status_code=502, detail=f"Google STT request failed: {exc}") from exc

    transcripts = []
    for result in response.results:
        if not result.alternatives:
            continue
        t = str(result.alternatives[0].transcript or "").strip()
        if t:
            transcripts.append(t)

    transcript = " ".join(transcripts).strip()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await _write_audit_log(
        request_id=request_id,
        service_name="interview",
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
        details={**_request_summary(req), "transcript_length": len(transcript)},
    )

    return {"transcript": transcript}


@app.on_event("startup")
async def _startup() -> None:
    await _ensure_audit_schema()
