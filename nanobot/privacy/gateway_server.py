"""Standalone local privacy gateway server."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from nanobot.config.schema import PrivacyGatewayConfig
from nanobot.privacy.gateway import PrivacyGatewayService


class PrivacyGatewayHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying one privacy gateway service instance."""

    def __init__(self, server_address: tuple[str, int], handler_class, service: PrivacyGatewayService):
        super().__init__(server_address, handler_class)
        self.service = service


class PrivacyGatewayHandler(BaseHTTPRequestHandler):
    """Serve OpenAI-compatible chat completions behind the local privacy gateway."""

    server: PrivacyGatewayHTTPServer

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/healthz":
            self._write_json(200, {"ok": True})
            return
        self._write_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._write_json(404, {"error": "not_found"})
            return

        # Privacy pipeline step 3:
        # terminate OpenAI-compatible traffic locally, parse the JSON body,
        # then delegate the real privacy logic to PrivacyGatewayService.

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""

        try:
            payload = json.loads(raw.decode("utf-8") if raw else "{}")
        except json.JSONDecodeError:
            self._write_json(400, {"error": "invalid_json"})
            return

        response = self.server.service.handle_chat_completions(payload, headers=dict(self.headers.items()))
        self.send_response(response.status_code)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        self.wfile.write(response.body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        """Keep the local gateway quiet during normal use."""
        return

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(
    *,
    host: str,
    port: int,
    upstream_base: str,
    workspace: Path,
    config: PrivacyGatewayConfig,
) -> PrivacyGatewayHTTPServer:
    """Build a privacy gateway HTTP server for production or tests."""
    service = PrivacyGatewayService(upstream_base=upstream_base, workspace=workspace, config=config)
    return PrivacyGatewayHTTPServer((host, port), PrivacyGatewayHandler, service)


def main() -> None:
    """Run the privacy gateway using environment configuration."""
    # Privacy pipeline step 3a: the CLI passes the true upstream URL and the
    # privacy flags through environment variables, and this process rebuilds
    # a runtime PrivacyGatewayConfig from them.
    upstream_base = os.environ["NANOBOT_PRIVACY_UPSTREAM_BASE"]
    workspace = Path(os.environ["NANOBOT_PRIVACY_WORKSPACE"])
    host = os.environ.get("NANOBOT_PRIVACY_LISTEN_HOST", "127.0.0.1")
    port = int(os.environ.get("NANOBOT_PRIVACY_LISTEN_PORT", "8787"))
    config = PrivacyGatewayConfig(
        enabled=True,
        listen_host=host,
        listen_port=port,
        fail_closed=os.environ.get("NANOBOT_PRIVACY_FAIL_CLOSED", "true").lower() not in {"0", "false", "no"},
        save_redacted_debug=os.environ.get("NANOBOT_PRIVACY_SAVE_REDACTED_DEBUG", "true").lower() not in {"0", "false", "no"},
        text_only_scope=os.environ.get("NANOBOT_PRIVACY_TEXT_ONLY_SCOPE", "true").lower() not in {"0", "false", "no"},
        enable_ner_assist=os.environ.get("NANOBOT_PRIVACY_ENABLE_NER_ASSIST", "false").lower() in {"1", "true", "yes"},
    )
    server = build_server(
        host=host,
        port=port,
        upstream_base=upstream_base,
        workspace=workspace,
        config=config,
    )
    try:
        server.serve_forever()
    finally:
        server.service.close()
        server.server_close()


if __name__ == "__main__":
    main()
