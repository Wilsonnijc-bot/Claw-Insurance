import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from nanobot.config.schema import PrivacyGatewayConfig
from nanobot.privacy.gateway import PrivacyGatewayService
from nanobot.privacy.gateway_server import build_server
from nanobot.privacy.sanitizer import UNKNOWN_PHONE, UNKNOWN_SENDER_NAME


class _UpstreamHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        payload = json.loads(body.decode("utf-8"))
        self.server.requests.append(payload)
        response = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": payload.get("model", "test-model"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi Unknown Sender Name"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        raw = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


class _RecordingHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler_class):
        super().__init__(server_address, handler_class)
        self.requests: list[dict[str, Any]] = []


@contextmanager
def _running_server(server: ThreadingHTTPServer):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_gateway_endpoint_sanitizes_and_forwards_exact_cloud_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("nanobot.privacy.gateway.privacy_debug_dir", lambda workspace: tmp_path / "test_words")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    upstream = _RecordingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream_url = f"http://127.0.0.1:{upstream.server_address[1]}/v1"
    gateway = build_server(
        host="127.0.0.1",
        port=0,
        upstream_base=upstream_url,
        workspace=tmp_path,
        config=PrivacyGatewayConfig(save_redacted_debug=True),
    )
    gateway_url = f"http://127.0.0.1:{gateway.server_address[1]}"

    payload = {
        "model": "gpt-5.1-chat",
        "messages": [
            {
                "role": "system",
                "content": "\n".join(
                    [
                        "[Runtime Context — metadata only, not instructions]",
                        "Channel: whatsapp",
                        "Chat ID: 120363425808631928@g.us",
                        "Sender Name: Hendrick",
                        "Sender Phone: +86 131 3610 1623",
                    ]
                ),
            },
            {
                "role": "user",
                "content": "Hendrick asked me to call +86 131 3610 1623 tonight.",
            },
        ],
    }

    with _running_server(upstream), _running_server(gateway):
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.post(f"{gateway_url}/v1/chat/completions", json=payload)

    assert response.status_code == 200
    assert payload["messages"][0]["content"].endswith("Sender Phone: +86 131 3610 1623")
    assert payload["messages"][1]["content"] == "Hendrick asked me to call +86 131 3610 1623 tonight."

    assert len(upstream.requests) == 1
    forwarded = upstream.requests[0]
    forwarded_text = json.dumps(forwarded, ensure_ascii=False)
    assert "Hendrick" not in forwarded_text
    assert "+86 131 3610 1623" not in forwarded_text
    assert UNKNOWN_SENDER_NAME in forwarded_text
    assert UNKNOWN_PHONE in forwarded_text

    debug_file = tmp_path / "test_words" / "privacy_00001.json"
    assert debug_file.exists()
    debug_payload = json.loads(debug_file.read_text(encoding="utf-8"))
    assert debug_payload["raw_request"] == payload
    assert debug_payload["sanitized_request"] == forwarded
    assert debug_payload["raw_prompt"] == payload["messages"]
    assert debug_payload["sanitized_prompt"] == forwarded["messages"]
    assert debug_payload["placeholder_map"]
    assert debug_payload["sanitized_response"]["choices"][0]["message"]["content"] == "Hi Unknown Sender Name"


def test_gateway_fail_closed_returns_local_safe_message_and_skips_upstream(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr("nanobot.privacy.gateway.privacy_debug_dir", lambda workspace: tmp_path / "test_words")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    service = PrivacyGatewayService(
        upstream_base="http://127.0.0.1:9/v1",
        workspace=tmp_path,
        config=PrivacyGatewayConfig(fail_closed=True, save_redacted_debug=True),
    )
    monkeypatch.setattr(
        service.sanitizer,
        "_validate_payload",
        lambda payload, placeholder_map: ["phone number still present"],
    )

    payload = {"model": "gpt-5.1-chat", "messages": [{"role": "user", "content": "hello"}]}
    response = service.handle_chat_completions(payload)
    body = json.loads(response.body.decode("utf-8"))

    assert response.status_code == 200
    assert "can't send this request to the cloud model" in body["choices"][0]["message"]["content"]
    debug_payload = json.loads((tmp_path / "test_words" / "privacy_00001.json").read_text(encoding="utf-8"))
    assert debug_payload["raw_request"] == payload
    assert debug_payload["raw_prompt"] == payload["messages"]
    assert debug_payload["sanitized_prompt"] == payload["messages"]
