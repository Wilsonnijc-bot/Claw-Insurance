"""Small HTTP server that emulates the project's retired cloud APIs.

The server deliberately records only request metadata. Prompt text, audio,
authorization headers, and client identifiers are never stored in its journal.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


MAX_BODY_BYTES = 20 * 1024 * 1024
REQUESTS: deque[dict[str, Any]] = deque(maxlen=100)

DEMO_TABLES: dict[str, list[dict[str, str]]] = {
    "insurance_products": [
        {
            "plan_id": "DEMO-MED-001",
            "plan_name": "安心医疗演示计划",
            "provider_company": "Demo Insurance",
            "plan_category": "医疗保险",
            "coverage_description": "演示住院、手术及门诊保障查询流程。",
            "pricing": "演示保费：每月 HKD 500 起",
            "age": "18-65",
            "customer_requirement": "希望获得基础医疗保障的演示客户",
            "price_structure": "仅供 Demo，不构成真实报价",
            "additional_informations": "此数据来自本地 Mock DB API。",
            "product_brochure_route": "demo://brochures/medical",
            "url": "https://example.invalid/demo-medical",
        },
        {
            "plan_id": "DEMO-SAV-001",
            "plan_name": "稳健储蓄演示计划",
            "provider_company": "Demo Insurance",
            "plan_category": "储蓄保险",
            "coverage_description": "演示长期储蓄及保障组合查询流程。",
            "pricing": "演示供款：每月 HKD 1,000 起",
            "age": "18-60",
            "customer_requirement": "希望演示长期财务规划的客户",
            "price_structure": "仅供 Demo，不构成真实报价",
            "additional_informations": "固定响应可证明数据库代理调用成功。",
            "product_brochure_route": "demo://brochures/savings",
            "url": "https://example.invalid/demo-savings",
        },
    ],
    "dental_insurance": [
        {
            "plan_id": "DEMO-DEN-001",
            "plan_name": "齿科护理演示计划",
            "provider_company": "Demo Insurance",
            "plan_category": "牙科保险",
            "coverage_description": "演示洗牙、检查及基础牙科治疗保障。",
            "pricing": "演示保费：每月 HKD 120 起",
            "age": "6-70",
            "customer_requirement": "需要牙科保障演示的客户",
            "price_structure": "仅供 Demo，不构成真实报价",
            "additional_informations": "此数据不会连接真实 Supabase。",
            "product_brochure_route": "demo://brochures/dental",
            "url": "https://example.invalid/demo-dental",
        }
    ],
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _record(kind: str, **metadata: Any) -> str:
    request_id = f"mock-{uuid.uuid4().hex[:12]}"
    REQUESTS.append(
        {
            "request_id": request_id,
            "timestamp": _now(),
            "kind": kind,
            **metadata,
        }
    )
    return request_id


class MockCloudHandler(BaseHTTPRequestHandler):
    server_version = "ClawInsuranceMock/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Avoid BaseHTTPRequestHandler logging authorization-adjacent request data.
        print(f"[mock-cloud] {self.command} {self.path} -> {args[1] if len(args) > 1 else '-'}")

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any] | None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(400, {"error": "invalid Content-Length"})
            return None
        if size <= 0 or size > MAX_BODY_BYTES:
            self._send_json(413, {"error": "request body is empty or too large"})
            return None
        try:
            payload = json.loads(self.rfile.read(size).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "request body must be a JSON object"})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "request body must be a JSON object"})
            return None
        return payload

    def _authorized(self, env_name: str, fallback: str) -> bool:
        expected = os.environ.get(env_name, fallback)
        supplied = self.headers.get("Authorization", "")
        if supplied == f"Bearer {expected}":
            return True
        self._send_json(401, {"error": "invalid demo API key"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "service": "claw-insurance-mock-cloud",
                    "time": _now(),
                },
            )
            return
        if self.path == "/requests":
            self._send_json(200, {"requests": list(REQUESTS)})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/query":
            self._handle_query()
            return
        if self.path == "/recognize":
            self._handle_recognize()
            return
        if self.path == "/v1/chat/completions":
            self._handle_chat_completion()
            return
        self._send_json(404, {"error": "not found"})

    def _handle_query(self) -> None:
        if not self._authorized("MOCK_DB_API_KEY", "demo-db-key"):
            return
        payload = self._read_json()
        if payload is None:
            return
        if payload.get("query_type") != "select":
            self._send_json(400, {"error": "demo DB supports read-only select queries"})
            return
        table = str(payload.get("table", ""))
        if table not in DEMO_TABLES:
            self._send_json(404, {"error": f"unknown demo table: {table}"})
            return
        try:
            limit = min(max(int(payload.get("limit", 1000)), 1), 1000)
            offset = max(int(payload.get("offset", 0)), 0)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "limit and offset must be integers"})
            return
        rows = DEMO_TABLES[table][offset : offset + limit]
        request_id = _record("database", table=table, row_count=len(rows))
        self._send_json(
            200,
            {
                "rows": rows,
                "mock": True,
                "request_id": request_id,
                "message": "本地 Mock DB API 请求成功",
            },
        )

    def _handle_recognize(self) -> None:
        if not self._authorized("MOCK_INTERVIEW_API_KEY", "demo-interview-key"):
            return
        payload = self._read_json()
        if payload is None:
            return
        audio = payload.get("audio_base64")
        if not isinstance(audio, str) or not audio:
            self._send_json(400, {"error": "audio_base64 is required"})
            return
        try:
            audio_bytes = base64.b64decode(audio, validate=True)
        except ValueError:
            self._send_json(400, {"error": "audio_base64 is invalid"})
            return
        request_id = _record(
            "speech",
            language=str(payload.get("language", "")),
            audio_bytes=len(audio_bytes),
        )
        self._send_json(
            200,
            {
                "transcript": "这是本地模拟语音 API 返回的会议记录，说明录音请求链路已经成功。",
                "mock": True,
                "request_id": request_id,
            },
        )

    def _handle_chat_completion(self) -> None:
        if not self._authorized("MOCK_AI_API_KEY", "demo-ai-key"):
            return
        payload = self._read_json()
        if payload is None:
            return
        model = str(payload.get("model", "demo-model"))
        messages = payload.get("messages")
        message_count = len(messages) if isinstance(messages, list) else 0
        request_id = _record("ai", model=model, message_count=message_count)
        self._send_json(
            200,
            {
                "id": request_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "这是本地 Mock AI 的固定回复，说明 OpenAI 兼容请求链路已经成功。",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )


def create_server(host: str = "0.0.0.0", port: int = 5050) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), MockCloudHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claw Insurance local mock cloud APIs")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args(argv)
    server = create_server(args.host, args.port)
    print(f"[mock-cloud] listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
