"""Local OpenAI-compatible privacy gateway."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from nanobot.config.schema import PrivacyGatewayConfig
from nanobot.privacy.sanitizer import SanitizationResult, TextPrivacySanitizer, load_known_names, privacy_debug_dir


@dataclass
class GatewayResponse:
    """Normalized gateway response."""

    status_code: int
    body: bytes
    content_type: str = "application/json"


class PrivacyDebugStore:
    """Persist sanitized request/response payloads locally.

    Privacy pipeline step 4 side effect: write gateway-facing artifacts into
    ``state/debug/privacy/privacy_XXXXX.json`` so the sanitized request
    diff can be audited locally without exposing the raw outbound payload to
    the cloud.
    """

    def __init__(self, workspace: Path):
        self.dir = privacy_debug_dir(workspace)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.counter_file = self.dir / ".privacy_counter"
        if not self.counter_file.exists():
            self.counter_file.write_text("0\n", encoding="utf-8")

    def write(
        self,
        *,
        result: SanitizationResult,
        upstream_url: str,
        response_status: int,
        response_body: Any,
    ) -> Path:
        current = 0
        try:
            raw = self.counter_file.read_text(encoding="utf-8").strip()
            current = int(raw) if raw else 0
        except (OSError, ValueError):
            current = 0
        next_index = current + 1
        self.counter_file.write_text(f"{next_index}\n", encoding="utf-8")
        path = self.dir / f"privacy_{next_index:05d}.json"
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_key": f"session:{hashlib.sha256(result.session_key.encode('utf-8')).hexdigest()[:12]}",
            "blocked": result.blocked,
            "reasons": result.reasons,
            "upstream_url": upstream_url,
            # Do not persist the reverse mapping: its keys are the raw PII.
            "sanitized_request": result.sanitized_payload,
            "sanitized_prompt": result.sanitized_payload.get("messages"),
            "response_status": response_status,
            "sanitized_response": response_body,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


class PrivacyGatewayService:
    """HTTP-facing service that sanitizes and forwards chat-completions requests."""

    def __init__(
        self,
        *,
        upstream_base: str,
        workspace: Path,
        config: PrivacyGatewayConfig,
    ):
        self.upstream_base = upstream_base.rstrip("/")
        self.workspace = workspace
        self.config = config
        self.sanitizer = TextPrivacySanitizer(config, known_names=load_known_names(workspace))
        self.debug_store = PrivacyDebugStore(workspace)
        self._client = httpx.Client(timeout=120.0)

    def close(self) -> None:
        """Release HTTP resources."""
        self._client.close()

    def handle_chat_completions(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> GatewayResponse:
        # Privacy pipeline step 4:
        # sanitize the outbound payload first, then either block locally or
        # forward only the sanitized copy to the real upstream endpoint.
        result = self.sanitizer.sanitize_chat_payload(payload, headers=headers)
        model = str(payload.get("model") or "unknown")

        if result.blocked:
            # Fail-closed behavior: do not call the cloud when validation still
            # sees risky text after masking.
            blocked = TextPrivacySanitizer.build_blocked_response(model=model)
            if self.config.save_redacted_debug:
                self.debug_store.write(
                    result=result,
                    upstream_url=f"{self.upstream_base}/chat/completions",
                    response_status=200,
                    response_body=blocked,
                )
            return GatewayResponse(status_code=200, body=json.dumps(blocked).encode("utf-8"))

        upstream_headers = self._build_upstream_headers(headers)
        # Only the sanitized payload is sent upstream.
        response = self._client.post(
            f"{self.upstream_base}/chat/completions",
            json=result.sanitized_payload,
            headers=upstream_headers,
        )

        response_body: Any
        content_type = response.headers.get("content-type", "application/json")
        try:
            response_body = response.json()
            response_body = self._sanitize_cloud_response(response_body, headers=headers)
            raw_body = json.dumps(response_body).encode("utf-8")
            content_type = "application/json"
        except ValueError:
            response_body = response.text
            raw_body = response.content

        if self.config.save_redacted_debug:
            # Persist the sanitized request/response pair for inspection.
            self.debug_store.write(
                result=result,
                upstream_url=f"{self.upstream_base}/chat/completions",
                response_status=response.status_code,
                response_body=response_body,
            )

        return GatewayResponse(status_code=response.status_code, body=raw_body, content_type=content_type)

    def _sanitize_cloud_response(
        self,
        response_body: Any,
        *,
        headers: dict[str, str] | None,
    ) -> Any:
        """Apply the same deterministic PII filter to cloud model output.

        This happens locally before the Agent sees assistant text or tool
        arguments.  The WhatsApp channel may later restore the one verified
        sender-name placeholder from local message metadata.
        """
        if not isinstance(response_body, dict):
            return response_body
        choices = response_body.get("choices")
        if not isinstance(choices, list):
            return response_body

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, (str, list, dict)):
                result = self.sanitizer.sanitize_chat_payload(
                    {"messages": [{"role": "assistant", "content": content}]},
                    headers=headers,
                )
                sanitized = result.sanitized_payload.get("messages") or []
                if sanitized:
                    message["content"] = sanitized[0].get("content")

            for tool_call in message.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict) or not isinstance(function.get("arguments"), str):
                    continue
                result = self.sanitizer.sanitize_chat_payload(
                    {"messages": [{"role": "assistant", "content": function["arguments"]}]},
                    headers=headers,
                )
                sanitized = result.sanitized_payload.get("messages") or []
                if sanitized:
                    function["arguments"] = sanitized[0].get("content", function["arguments"])
        return response_body

    @staticmethod
    def _build_upstream_headers(headers: dict[str, str] | None) -> dict[str, str]:
        # Allow auth and org headers through but strip x-session-affinity
        # to prevent the stable process-lifetime UUID from becoming a
        # cross-session correlation token at the cloud provider.
        allowed = {"authorization", "openai-organization", "openai-project"}
        forwarded: dict[str, str] = {}
        for key, value in (headers or {}).items():
            if key.lower() in allowed:
                forwarded[key] = value
        return forwarded
