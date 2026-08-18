from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp import WhatsAppChannel
from nanobot.config.schema import PrivacyGatewayConfig, WhatsAppConfig
from nanobot.privacy.sanitizer import TextPrivacySanitizer
from nanobot.providers.litellm_provider import LiteLLMProvider


def test_draft_history_removes_only_trailing_duplicate_user_turn() -> None:
    history = [
        {"role": "user", "content": "same"},
        {"role": "assistant", "content": "earlier reply"},
        {"role": "user", "content": "same"},
    ]

    assert AgentLoop._without_duplicate_current_message(history, "same") == history[:-1]
    assert AgentLoop._without_duplicate_current_message(history, "different") == history


def test_text_only_privacy_scope_blocks_images() -> None:
    sanitizer = TextPrivacySanitizer(
        PrivacyGatewayConfig(fail_closed=True, text_only_scope=True)
    )
    result = sanitizer.sanitize_chat_payload(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "please inspect"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    ],
                }
            ]
        },
        headers={"x-session-affinity": "whatsapp:10001"},
    )

    assert result.blocked is True
    assert "image content cannot be privacy-sanitized" in result.reasons


@pytest.mark.asyncio
async def test_litellm_affinity_header_is_loopback_only(monkeypatch) -> None:
    captured: list[dict] = []

    async def fake_completion(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="ok", tool_calls=None),
                    finish_reason="stop",
                )
            ]
        )

    monkeypatch.setattr("nanobot.providers.litellm_provider.acompletion", fake_completion)
    local = LiteLLMProvider(api_base="http://127.0.0.1:8787/v1", default_model="openai/test")
    await local.chat([{"role": "user", "content": "hello"}], session_affinity="whatsapp:10001")
    cloud = LiteLLMProvider(api_base="https://example.invalid/v1", default_model="openai/test")
    await cloud.chat([{"role": "user", "content": "hello"}], session_affinity="whatsapp:10002")

    assert captured[0]["extra_headers"]["x-session-affinity"] == "whatsapp:10001"
    assert "extra_headers" not in captured[1]


class _RecordingWS:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def send(self, raw: str) -> None:
        self.payloads.append(json.loads(raw))


@pytest.mark.asyncio
async def test_whatsapp_send_confirmation_correlates_bridge_ack(tmp_path: Path) -> None:
    config = WhatsAppConfig(
        enabled=True,
        delivery_mode="draft",
        web_browser_mode="cdp",
        reply_targets_file=str(tmp_path / "targets.json"),
    )
    channel = WhatsAppChannel(config, MessageBus(), workspace=tmp_path)
    ws = _RecordingWS()
    channel._ws = ws
    channel._connected = True
    message = OutboundMessage(
        channel="whatsapp",
        chat_id="10001@s.whatsapp.net",
        content="approved",
        metadata={"_human_approved_send": True, "_send_request_id": "send_test"},
    )

    pending = asyncio.create_task(channel.send_confirmed(message, timeout_s=1))
    while not ws.payloads:
        await asyncio.sleep(0)
    assert ws.payloads[0]["requestId"] == "send_test"
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "send",
                "to": message.chat_id,
                "requestId": "send_test",
                "status": "accepted",
            }
        )
    )

    assert (await pending)["status"] == "accepted"
