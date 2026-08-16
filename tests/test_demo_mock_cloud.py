from __future__ import annotations

import base64
import json
import threading
from http.client import HTTPConnection

from nanobot.demo.mock_cloud import create_server


def _request(port: int, method: str, path: str, payload=None, token: str = ""):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    conn = HTTPConnection("127.0.0.1", port, timeout=2)
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read().decode())
    conn.close()
    return response.status, data


def test_mock_cloud_contracts_and_redacted_journal():
    server = create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        status, health = _request(port, "GET", "/healthz")
        assert status == 200
        assert health["service"] == "claw-insurance-mock-cloud"

        status, _ = _request(
            port,
            "POST",
            "/query",
            {"query_type": "select", "table": "insurance_products"},
            "wrong-key",
        )
        assert status == 401

        status, database = _request(
            port,
            "POST",
            "/query",
            {"query_type": "select", "table": "insurance_products", "limit": 10},
            "demo-db-key",
        )
        assert status == 200
        assert database["mock"] is True
        assert database["rows"][0]["plan_id"] == "DEMO-MED-001"

        status, speech = _request(
            port,
            "POST",
            "/recognize",
            {
                "audio_base64": base64.b64encode(b"demo-audio").decode(),
                "language": "yue-Hant-HK",
            },
            "demo-interview-key",
        )
        assert status == 200
        assert "模拟语音" in speech["transcript"]

        private_prompt = "private client text must not be journaled"
        status, completion = _request(
            port,
            "POST",
            "/v1/chat/completions",
            {"model": "demo-model", "messages": [{"role": "user", "content": private_prompt}]},
            "demo-ai-key",
        )
        assert status == 200
        assert completion["choices"][0]["finish_reason"] == "stop"

        status, journal = _request(port, "GET", "/requests")
        assert status == 200
        serialized = json.dumps(journal)
        assert private_prompt not in serialized
        assert {item["kind"] for item in journal["requests"]} >= {"database", "speech", "ai"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
