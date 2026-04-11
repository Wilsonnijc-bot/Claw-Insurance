import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import HistoryImportResult, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp import WhatsAppChannel
from nanobot.channels.whatsapp_reply_targets import (
    load_reply_targets,
    observe_direct_identification,
    observe_group_identification,
    rewrite_from_self_instruction,
    save_reply_targets,
)
from nanobot.config.schema import Config, WhatsAppConfig
from nanobot.session.manager import SessionManager


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        return None


def _make_channel(
    config: WhatsAppConfig | None = None,
    workspace: Path | None = None,
    bus: MessageBus | None = None,
) -> WhatsAppChannel:
    return WhatsAppChannel(
        config or WhatsAppConfig(
            enabled=True,
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
        ),
        bus or MessageBus(),
        workspace=workspace,
    )


def _persist_session_messages(workspace: Path, phone: str, message_ids: list[str]) -> None:
    manager = SessionManager(workspace)
    session = manager.get_or_create(f"whatsapp:{phone}")
    existing_ids = {
        str(message.get("message_id", "") or "").strip()
        for message in session.messages
        if str(message.get("message_id", "") or "").strip()
    }
    for index, message_id in enumerate(message_ids):
        if message_id in existing_ids:
            continue
        session.add_message(
            "client",
            f"persisted message {index}",
            message_id=message_id,
            chat_id=f"{phone}@s.whatsapp.net",
            sender_id=phone,
            sender_phone=phone,
            historical_import=True,
        )
    manager.save(session)


def _make_history_import_loop(monkeypatch: pytest.MonkeyPatch, workspace: Path, bus: MessageBus) -> AgentLoop:
    monkeypatch.setattr(AgentLoop, "_ensure_test_words_dir", lambda self: None)
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called in history-only tests"))
    return AgentLoop(bus=bus, provider=provider, workspace=workspace, model="test-model")


def test_whatsapp_config_accepts_draft_fields() -> None:
    config = Config.model_validate(
        {
            "channels": {
                "whatsapp": {
                    "enabled": True,
                    "deliveryMode": "draft",
                    "webBrowserMode": "cdp",
                    "webCdpUrl": "http://127.0.0.1:9333",
                    "webProfileDir": "~/custom-whatsapp-web",
                    "groupMembersFile": "~/groups.csv",
                    "replyTargetsFile": "~/data/reply_targets.json",
                    "allowFrom": ["+1234567890"],
                }
            }
        }
    )

    assert config.channels.whatsapp.delivery_mode == "draft"
    assert config.channels.whatsapp.web_browser_mode == "cdp"
    assert config.channels.whatsapp.web_cdp_url == "http://127.0.0.1:9333"
    assert config.channels.whatsapp.web_profile_dir == "~/custom-whatsapp-web"
    assert not hasattr(config.channels.whatsapp, "contacts_file")
    assert config.channels.whatsapp.group_members_file == "~/groups.csv"
    assert config.channels.whatsapp.reply_targets_file == "~/data/reply_targets.json"


def test_whatsapp_config_defaults_to_draft() -> None:
    assert WhatsAppConfig().delivery_mode == "draft"
    assert WhatsAppConfig().web_browser_mode == "cdp"


@pytest.mark.asyncio
async def test_whatsapp_send_mode_emits_send_command() -> None:
    channel = _make_channel(
        WhatsAppConfig(enabled=True, delivery_mode="send", allow_from=["+1234567890"], contacts_file="", group_members_file="")
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(OutboundMessage(channel="whatsapp", chat_id="123@s.whatsapp.net", content="hello"))

    assert [json.loads(item) for item in ws.sent] == [
        {"type": "send", "to": "123@s.whatsapp.net", "text": "hello"}
    ]


@pytest.mark.asyncio
async def test_whatsapp_send_restores_only_sender_name_placeholder() -> None:
    channel = _make_channel(
        WhatsAppConfig(enabled=True, delivery_mode="send", allow_from=["+1234567890"], contacts_file="", group_members_file="")
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="123@s.whatsapp.net",
            content="Hi Unknown Sender Name, call Unknown Phone Number",
            metadata={"sender_name": "Hendrick"},
        )
    )

    assert [json.loads(item) for item in ws.sent] == [
        {
            "type": "send",
            "to": "123@s.whatsapp.net",
            "text": "Hi Hendrick, call Unknown Phone Number",
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_launch_draft_mode_emits_prepare_draft_command_with_reply_target(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            web_browser_mode="launch",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="123@s.whatsapp.net",
            content="draft me",
            metadata={"sender": "123@s.whatsapp.net", "sender_phone": "+1234567890"},
        )
    )

    assert [json.loads(item) for item in ws.sent] == [
        {
            "type": "prepare_draft",
            "to": "123@s.whatsapp.net",
            "text": "draft me",
            "target": {
                "chatId": "123@s.whatsapp.net",
                "phone": "1234567890",
                "searchTerms": ["123", "Alice Chan", "1234567890"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_skips_progress_updates() -> None:
    channel = _make_channel(
        WhatsAppConfig(enabled=True, delivery_mode="draft", allow_from=["+1234567890"], contacts_file="", group_members_file="")
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="123@s.whatsapp.net",
            content="partial",
            metadata={"_progress": True},
        )
    )

    assert ws.sent == []


@pytest.mark.asyncio
async def test_whatsapp_cdp_draft_mode_skips_prepare_draft_command(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            web_browser_mode="cdp",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="alice@lid",
            content="draft me",
            metadata={"sender": "alice@lid", "sender_phone": "+1234567890"},
        )
    )

    assert ws.sent == []


@pytest.mark.asyncio
async def test_whatsapp_launch_draft_mode_emits_prepare_draft_command_for_phone_only_target(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            web_browser_mode="launch",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="alice@lid",
            content="draft me",
            metadata={"sender": "alice@lid", "sender_phone": "+1234567890"},
        )
    )

    assert [json.loads(item) for item in ws.sent] == [
        {
            "type": "prepare_draft",
            "to": "alice@lid",
            "text": "draft me",
            "target": {
                "chatId": "alice@lid",
                "phone": "1234567890",
                "searchTerms": ["alice", "1234567890"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_launch_draft_mode_uses_reply_target_label_as_search_fallback(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    payload = load_reply_targets(targets_file)
    payload["direct_reply_targets"][0]["label"] = "Alice Wong"
    save_reply_targets(targets_file, payload)
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            web_browser_mode="launch",
            allow_from=["+1234567890"],
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="alice@lid",
            content="draft me",
            metadata={"sender": "alice@lid", "sender_phone": "+1234567890"},
        )
    )

    assert json.loads(ws.sent[0])["target"]["searchTerms"] == ["alice", "Alice Wong", "1234567890"]


@pytest.mark.asyncio
async def test_whatsapp_launch_draft_mode_includes_reply_target_label_in_search_terms(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    payload = load_reply_targets(targets_file)
    payload["direct_reply_targets"][0]["label"] = "Billy"
    save_reply_targets(targets_file, payload)
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            web_browser_mode="launch",
            allow_from=["+1234567890"],
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="123@s.whatsapp.net",
            content="draft me",
            metadata={"sender": "123@s.whatsapp.net", "sender_phone": "+1234567890"},
        )
    )

    assert json.loads(ws.sent[0])["target"]["searchTerms"] == ["123", "Alice Chan", "Billy", "1234567890"]


@pytest.mark.asyncio
async def test_whatsapp_parse_reply_targets_once_requests_bulk_direct_parse(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    payload = load_reply_targets(targets_file)
    payload["direct_reply_targets"][0]["label"] = "Alice Wong"
    save_reply_targets(targets_file, payload)
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    task = asyncio.create_task(channel.parse_reply_targets_once(timeout_s=1.0))
    await asyncio.sleep(0)

    sent = json.loads(ws.sent[0])
    request_id = sent["requestId"]
    assert sent == {
        "type": "scrape_reply_targets_history",
        "requestId": request_id,
        "targets": [
            {
                "chatId": "123@s.whatsapp.net",
                "phone": "1234567890",
                "searchTerms": ["123", "Alice Chan", "Alice Wong", "1234567890"],
            },
        ],
    }

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "scrape_reply_targets_history",
                "requestId": request_id,
                "status": "history_scraped",
                "scrapedTargets": 1,
                "scrapedMessages": 1,
                "missedTargets": 0,
                "importPhones": ["1234567890"],
            }
        )
    )
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=1,
            imported_entries=1,
            verified_entries=1,
            phones=["1234567890"],
            verified_phones=["1234567890"],
            metadata={"request_id": request_id},
        )
    )

    result = await asyncio.wait_for(task, timeout=1)
    channel._replay_cached_history.assert_awaited_once_with(["1234567890"])
    assert result["status"] == "history_scraped"
    assert result["requested_targets"] == 1
    assert result["scraped_targets"] == 1
    assert result["verified_targets"] == 1


@pytest.mark.asyncio
async def test_whatsapp_parse_reply_targets_once_coalesces_duplicate_inflight_direct_parse_requests(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._running = True
    channel._replay_cached_history = AsyncMock()
    worker = asyncio.create_task(channel._history_parse_worker())

    try:
        task_one = asyncio.create_task(channel.parse_reply_targets_once(timeout_s=1.0))
        await asyncio.sleep(0)
        task_two = asyncio.create_task(channel.parse_reply_targets_once(timeout_s=1.0))
        await asyncio.sleep(0)

        assert len(ws.sent) == 1
        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        assert sent["type"] == "scrape_reply_targets_history"

        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_reply_targets_history",
                    "requestId": request_id,
                    "status": "history_scraped",
                    "scrapedTargets": 1,
                    "scrapedMessages": 1,
                    "missedTargets": 0,
                    "importPhones": ["1234567890"],
                }
            )
        )
        await channel.bus.publish_history_result(
            HistoryImportResult(
                channel="whatsapp",
                matched_entries=1,
                imported_entries=1,
                verified_entries=1,
                phones=["1234567890"],
                verified_phones=["1234567890"],
                metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
            )
        )

        first, second = await asyncio.gather(task_one, task_two)
        assert first["status"] == "history_scraped"
        assert second["status"] == "history_scraped"
        assert first["request_id"] == second["request_id"]
    finally:
        channel._running = False
        channel._parse_queue_event.set()
        await worker


def test_whatsapp_build_scoped_direct_history_targets_payload_skips_rows_without_phone(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=None, groups=None)
    payload = load_reply_targets(targets_file)
    payload["direct_reply_targets"] = [
        {
            "phone": "",
            "label": "Missing Phone",
            "enabled": True,
            "chat_id": "123@s.whatsapp.net",
            "sender_id": "123@s.whatsapp.net",
            "push_name": "Alice Chan",
            "auto_draft": False,
            "last_seen_at": "",
        }
    ]
    save_reply_targets(targets_file, payload)

    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )

    assert channel._build_scoped_direct_history_targets_payload() == []


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_includes_reply_target_label_in_search_terms(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    payload = load_reply_targets(targets_file)
    payload["direct_reply_targets"][0]["label"] = "Billy"
    save_reply_targets(targets_file, payload)
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
    await asyncio.sleep(0)

    sent = json.loads(ws.sent[0])
    request_id = sent["requestId"]
    assert sent == {
        "type": "scrape_direct_history",
        "requestId": request_id,
        "target": {
            "chatId": "123@s.whatsapp.net",
            "phone": "1234567890",
            "searchTerms": ["123", "Alice Chan", "Billy", "1234567890"],
        },
    }

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "scrape_direct_history",
                "requestId": request_id,
                "status": "history_scraped",
                "scrapedTargets": 1,
                "scrapedMessages": 0,
                "missedTargets": 0,
                "importPhones": [],
            }
        )
    )
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "web_scrape",
                "requestId": request_id,
                "isLatest": True,
                "messages": [],
            }
        )
    )
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=0,
            imported_entries=0,
            verified_entries=0,
            phones=[],
            verified_phones=[],
            metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
        )
    )

    result = await asyncio.wait_for(task, timeout=1)
    assert result["status"] == "history_scraped"


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_coalesces_duplicate_manual_requests(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._running = True
    channel._replay_cached_history = AsyncMock()
    worker = asyncio.create_task(channel._history_parse_worker())

    try:
        task_one = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
        await asyncio.sleep(0)
        task_two = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
        await asyncio.sleep(0)

        assert len(ws.sent) == 1
        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        assert sent["type"] == "scrape_direct_history"

        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_direct_history",
                    "requestId": request_id,
                    "status": "history_scraped",
                    "scrapedTargets": 1,
                    "scrapedMessages": 1,
                    "missedTargets": 0,
                    "importPhones": ["1234567890"],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "messages": [
                        {
                            "id": "dup-sync-1",
                            "sender": "123@s.whatsapp.net",
                            "pn": "+1234567890",
                            "content": "older inbound",
                            "timestamp": 1700000000,
                            "fromMe": False,
                            "isGroup": False,
                            "pushName": "Alice Chan",
                        }
                    ],
                }
            )
        )
        _persist_session_messages(tmp_path, "1234567890", ["dup-sync-1"])
        await channel.bus.publish_history_result(
            HistoryImportResult(
                channel="whatsapp",
                matched_entries=1,
                imported_entries=1,
                verified_entries=1,
                phones=["1234567890"],
                verified_phones=["1234567890"],
                metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
            )
        )

        first, second = await asyncio.gather(task_one, task_two)
        assert first["status"] == "history_scraped"
        assert second["status"] == "history_scraped"
        assert first["request_id"] == second["request_id"]
    finally:
        channel._running = False
        channel._parse_queue_event.set()
        await worker


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_waits_for_import_confirmation(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
    await asyncio.sleep(0)

    sent = json.loads(ws.sent[0])
    request_id = sent["requestId"]
    assert sent["type"] == "scrape_direct_history"
    assert sent["target"]["chatId"] == "123@s.whatsapp.net"
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "scrape_direct_history",
                "requestId": request_id,
                "status": "history_scraped",
                "scrapedTargets": 1,
                "scrapedMessages": 2,
                "missedTargets": 0,
                "importPhones": ["1234567890"],
            }
        )
    )
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "web_scrape",
                "requestId": request_id,
                "messages": [
                    {
                        "id": "confirmed-1",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "content": "older inbound",
                        "timestamp": 1700000000,
                        "fromMe": False,
                        "isGroup": False,
                        "pushName": "Alice Chan",
                    },
                    {
                        "id": "confirmed-2",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "content": "older outbound",
                        "timestamp": 1700000001,
                        "fromMe": True,
                        "isGroup": False,
                    },
                ],
            }
        )
    )
    _persist_session_messages(tmp_path, "1234567890", ["confirmed-1", "confirmed-2"])
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=2,
            imported_entries=1,
            verified_entries=2,
            phones=["1234567890"],
            verified_phones=["1234567890"],
            metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
        )
    )

    result = await asyncio.wait_for(task, timeout=1)
    assert result["status"] == "history_scraped"
    assert result["matched_entries"] == 2
    assert result["imported_entries"] == 1
    assert result["verified_entries"] == 2
    assert result["verified_phones"] == ["1234567890"]
    assert result["backend_success"] is True


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_returns_non_success_when_final_import_is_unverified(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
    await asyncio.sleep(0)

    sent = json.loads(ws.sent[0])
    request_id = sent["requestId"]
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "scrape_direct_history",
                "requestId": request_id,
                "status": "history_scraped",
                "scrapedTargets": 1,
                "scrapedMessages": 2,
                "missedTargets": 0,
                "importPhones": ["1234567890"],
            }
        )
    )
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "web_scrape",
                "requestId": request_id,
                "messages": [
                    {
                        "id": "unverified-1",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "content": "older inbound",
                        "timestamp": 1700000000,
                        "fromMe": False,
                        "isGroup": False,
                        "pushName": "Alice Chan",
                    },
                    {
                        "id": "unverified-2",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "content": "older outbound",
                        "timestamp": 1700000001,
                        "fromMe": True,
                        "isGroup": False,
                    },
                ],
            }
        )
    )
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=2,
            imported_entries=0,
            verified_entries=0,
            phones=["1234567890"],
            verified_phones=[],
            metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
        )
    )

    result = await asyncio.wait_for(task, timeout=1)
    assert result["status"] == "history_scraped"
    assert result["matched_entries"] == 2
    assert result["imported_entries"] == 0
    assert result["verified_entries"] == 0
    assert result["verified_phones"] == []
    assert result["backend_success"] is False


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_jsonl_success_overrides_chat_not_found_after_cache_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    bus = MessageBus()
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
        bus=bus,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._history_cache["cache-race-1"] = {
        "id": "cache-race-1",
        "sender": "123@s.whatsapp.net",
        "pn": "+1234567890",
        "content": "older inbound",
        "timestamp": 1700000000,
        "fromMe": False,
        "isGroup": False,
        "pushName": "Alice Chan",
    }
    agent = _make_history_import_loop(monkeypatch, tmp_path, bus)
    await agent._processing_lock.acquire()
    worker = asyncio.create_task(agent.run())

    try:
        task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=0.5))
        await asyncio.sleep(0)

        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_direct_history",
                    "requestId": request_id,
                    "status": "chat_not_found",
                    "detail": "Chat 123@s.whatsapp.net is not available in WhatsApp Web search.",
                }
            )
        )

        await asyncio.sleep(0.75)
        assert not task.done()

        agent._processing_lock.release()
        result = await asyncio.wait_for(task, timeout=2)
    finally:
        if agent._processing_lock.locked():
            agent._processing_lock.release()
        agent.stop()
        worker.cancel()
        try:
            await worker
        except BaseException:
            pass

    assert result["status"] == "history_scraped"
    assert result["backend_success"] is True
    assert result["verified_entries"] == 1
    assert result["verified_phones"] == ["1234567890"]


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_jsonl_success_overrides_timeout_after_web_scrape_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    bus = MessageBus()
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
        bus=bus,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    agent = _make_history_import_loop(monkeypatch, tmp_path, bus)
    await agent._processing_lock.acquire()
    worker = asyncio.create_task(agent.run())

    try:
        task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=0.5))
        await asyncio.sleep(0)

        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_direct_history",
                    "requestId": request_id,
                    "status": "history_scraped",
                    "scrapedTargets": 1,
                    "scrapedMessages": 1,
                    "missedTargets": 0,
                    "importPhones": ["1234567890"],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "messages": [
                        {
                            "id": "timeout-race-1",
                            "sender": "123@s.whatsapp.net",
                            "pn": "+1234567890",
                            "content": "older inbound",
                            "timestamp": 1700000000,
                            "fromMe": False,
                            "isGroup": False,
                            "pushName": "Alice Chan",
                        }
                    ],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "isLatest": True,
                    "messages": [],
                }
            )
        )

        await asyncio.sleep(0.75)
        assert not task.done()

        agent._processing_lock.release()
        result = await asyncio.wait_for(task, timeout=2)
    finally:
        if agent._processing_lock.locked():
            agent._processing_lock.release()
        agent.stop()
        worker.cancel()
        try:
            await worker
        except BaseException:
            pass

    assert result["status"] == "history_scraped"
    assert result["backend_success"] is True
    assert result["verified_entries"] == 1
    assert result["verified_phones"] == ["1234567890"]


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_only_accepts_requested_phone_session_jsonl(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890", "+1987654321"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    observe_direct_identification(
        targets_file,
        phone="+1987654321",
        chat_id="456@s.whatsapp.net",
        sender_id="456@s.whatsapp.net",
        push_name="Bob Lee",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890", "+1987654321"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
    await asyncio.sleep(0)

    sent = json.loads(ws.sent[0])
    request_id = sent["requestId"]
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "scrape_direct_history",
                "requestId": request_id,
                "status": "history_scraped",
                "scrapedTargets": 1,
                "scrapedMessages": 1,
                "missedTargets": 0,
                "importPhones": ["1234567890"],
            }
        )
    )
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "web_scrape",
                "requestId": request_id,
                "messages": [
                    {
                        "id": "wrong-phone-1",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "content": "older inbound",
                        "timestamp": 1700000000,
                        "fromMe": False,
                        "isGroup": False,
                        "pushName": "Alice Chan",
                    }
                ],
            }
        )
    )
    _persist_session_messages(tmp_path, "1987654321", ["wrong-phone-1"])
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=1,
            imported_entries=0,
            verified_entries=0,
            phones=["1234567890"],
            verified_phones=[],
            metadata={"request_id": request_id, "source": "web_scrape"},
        )
    )
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=0,
            imported_entries=0,
            verified_entries=0,
            phones=[],
            verified_phones=[],
            metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
        )
    )

    result = await asyncio.wait_for(task, timeout=2)
    assert result["status"] == "history_scraped"
    assert result["backend_success"] is False
    assert result["verified_entries"] == 0
    assert result["verified_phones"] == []


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_waits_until_all_request_batches_are_processed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    bus = MessageBus()
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
        bus=bus,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    agent = _make_history_import_loop(monkeypatch, tmp_path, bus)
    import_gate = asyncio.Event()
    original_dispatch_history = agent._dispatch_history

    async def gated_dispatch_history(batch):
        if batch.metadata.get("source") == "web_scrape" and batch.metadata.get("request_id") and batch.entries:
            await import_gate.wait()
        return await original_dispatch_history(batch)

    monkeypatch.setattr(agent, "_dispatch_history", gated_dispatch_history)
    worker = asyncio.create_task(agent.run())

    try:
        task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=0.5))
        await asyncio.sleep(0)

        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_direct_history",
                    "requestId": request_id,
                    "status": "history_scraped",
                    "scrapedTargets": 1,
                    "scrapedMessages": 1,
                    "missedTargets": 0,
                    "importPhones": ["1234567890"],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "messages": [
                        {
                            "id": "ordered-batch-1",
                            "sender": "123@s.whatsapp.net",
                            "pn": "+1234567890",
                            "content": "older inbound",
                            "timestamp": 1700000000,
                            "fromMe": False,
                            "isGroup": False,
                            "pushName": "Alice Chan",
                        }
                    ],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "isLatest": True,
                    "messages": [],
                }
            )
        )

        await asyncio.sleep(0.75)
        assert not task.done()

        import_gate.set()
        result = await asyncio.wait_for(task, timeout=2)
    finally:
        agent.stop()
        worker.cancel()
        try:
            await worker
        except BaseException:
            pass

    assert result["status"] == "history_scraped"
    assert result["backend_success"] is True
    assert result["verified_entries"] == 1
    assert result["verified_phones"] == ["1234567890"]


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_does_not_timeout_while_request_batches_are_still_in_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    bus = MessageBus()
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
        bus=bus,
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    monkeypatch.setattr(channel, "_manual_sync_settle_grace_s", lambda _timeout_s: 0.2)

    agent = _make_history_import_loop(monkeypatch, tmp_path, bus)
    import_gate = asyncio.Event()
    original_dispatch_history = agent._dispatch_history

    async def gated_dispatch_history(batch):
        if batch.metadata.get("source") == "web_scrape" and batch.metadata.get("request_id") and batch.entries:
            await import_gate.wait()
        return await original_dispatch_history(batch)

    monkeypatch.setattr(agent, "_dispatch_history", gated_dispatch_history)
    worker = asyncio.create_task(agent.run())

    try:
        task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=0.5))
        await asyncio.sleep(0)

        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_direct_history",
                    "requestId": request_id,
                    "status": "history_scraped",
                    "scrapedTargets": 1,
                    "scrapedMessages": 1,
                    "missedTargets": 0,
                    "importPhones": ["1234567890"],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "messages": [
                        {
                            "id": "deadline-race-1",
                            "sender": "123@s.whatsapp.net",
                            "pn": "+1234567890",
                            "content": "older inbound",
                            "timestamp": 1700000000,
                            "fromMe": False,
                            "isGroup": False,
                            "pushName": "Alice Chan",
                        }
                    ],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "isLatest": True,
                    "messages": [],
                }
            )
        )

        await asyncio.sleep(0.8)
        assert not task.done()

        import_gate.set()
        result = await asyncio.wait_for(task, timeout=2)
    finally:
        agent.stop()
        worker.cancel()
        try:
            await worker
        except BaseException:
            pass

    assert result["status"] == "history_scraped"
    assert result["backend_success"] is True
    assert result["verified_entries"] == 1
    assert result["verified_phones"] == ["1234567890"]


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_waits_for_scrape_closure_before_deciding_full_intended_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    bus = MessageBus()
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        workspace=tmp_path,
        bus=bus,
    )
    channel._history_cache["mixed-replay-1"] = {
        "id": "mixed-replay-1",
        "sender": "123@s.whatsapp.net",
        "pn": "+1234567890",
        "content": "cached inbound",
        "timestamp": 1700000000,
        "fromMe": False,
        "isGroup": False,
        "pushName": "Alice Chan",
    }
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    agent = _make_history_import_loop(monkeypatch, tmp_path, bus)
    scrape_gate = asyncio.Event()
    scrape_done = asyncio.Event()
    original_dispatch_history = agent._dispatch_history

    async def gated_dispatch_history(batch):
        if batch.metadata.get("source") == "web_scrape" and batch.metadata.get("request_id"):
            if batch.entries:
                await scrape_gate.wait()
                try:
                    return await original_dispatch_history(batch)
                finally:
                    scrape_done.set()
            if bool(batch.metadata.get("isLatest")):
                await scrape_done.wait()
        return await original_dispatch_history(batch)

    monkeypatch.setattr(agent, "_dispatch_history", gated_dispatch_history)
    worker = asyncio.create_task(agent.run())

    try:
        task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=0.5))
        await asyncio.sleep(0)

        sent = json.loads(ws.sent[0])
        request_id = sent["requestId"]
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "ack",
                    "action": "scrape_direct_history",
                    "requestId": request_id,
                    "status": "history_scraped",
                    "scrapedTargets": 1,
                    "scrapedMessages": 1,
                    "missedTargets": 0,
                    "importPhones": ["1234567890"],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "messages": [
                        {
                            "id": "mixed-scrape-2",
                            "sender": "123@s.whatsapp.net",
                            "pn": "+1234567890",
                            "content": "scraped inbound",
                            "timestamp": 1700000001,
                            "fromMe": False,
                            "isGroup": False,
                            "pushName": "Alice Chan",
                        }
                    ],
                }
            )
        )
        await channel._handle_bridge_message(
            json.dumps(
                {
                    "type": "history",
                    "source": "web_scrape",
                    "requestId": request_id,
                    "isLatest": True,
                    "messages": [],
                }
            )
        )

        session_path = tmp_path / "sessions" / "whatsapp__1234567890" / "session.jsonl"
        for _ in range(20):
            if session_path.exists() and "mixed-replay-1" in session_path.read_text(encoding="utf-8"):
                break
            await asyncio.sleep(0.05)

        assert session_path.exists()
        assert "mixed-replay-1" in session_path.read_text(encoding="utf-8")
        assert "mixed-scrape-2" not in session_path.read_text(encoding="utf-8")

        await asyncio.sleep(0.75)
        assert not task.done()

        scrape_gate.set()
        result = await asyncio.wait_for(task, timeout=2)
    finally:
        agent.stop()
        worker.cancel()
        try:
            await worker
        except BaseException:
            pass

    assert result["status"] == "history_scraped"
    assert result["backend_success"] is True
    assert result["verified_entries"] == 2
    assert result["verified_phones"] == ["1234567890"]

@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_scopes_web_scrape_to_requested_phone(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890", "+1987654321"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    observe_direct_identification(
        targets_file,
        phone="+1987654321",
        chat_id="456@s.whatsapp.net",
        sender_id="456@s.whatsapp.net",
        push_name="Bob Lee",
    )
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890", "+1987654321"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    task = asyncio.create_task(channel.sync_direct_history(["1234567890"], timeout_s=1.0))
    await asyncio.sleep(0)

    sent = json.loads(ws.sent[0])
    request_id = sent["requestId"]
    assert sent == {
        "type": "scrape_direct_history",
        "requestId": request_id,
        "target": {
            "chatId": "123@s.whatsapp.net",
            "phone": "1234567890",
            "searchTerms": ["123", "Alice Chan", "1234567890"],
        },
    }

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "ack",
                "action": "scrape_direct_history",
                "requestId": request_id,
                "status": "history_scraped",
                "scrapedTargets": 1,
                "scrapedMessages": 0,
                "missedTargets": 0,
                "importPhones": [],
            }
        )
    )
    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "web_scrape",
                "requestId": request_id,
                "isLatest": True,
                "messages": [],
            }
        )
    )
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=0,
            imported_entries=0,
            verified_entries=0,
            phones=[],
            verified_phones=[],
            metadata={"request_id": request_id, "source": "web_scrape", "isLatest": True},
        )
    )

    result = await asyncio.wait_for(task, timeout=1)
    assert channel._replay_cached_history.await_count == 1
    replay_args, replay_kwargs = channel._replay_cached_history.await_args
    assert replay_args == (["1234567890"],)
    assert replay_kwargs["request_id"] == request_id
    assert result["status"] == "history_scraped"


@pytest.mark.asyncio
async def test_whatsapp_status_connected_does_not_queue_direct_history_sync() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, allow_from=["+1234567890"], contacts_file="", group_members_file=""),
        bus,
    )

    channel._set_bridge_status(True, "Bridge down")
    await channel._handle_bridge_message(json.dumps({"type": "status", "status": "connected"}))

    assert bus.outbound_size == 0
    assert channel.get_bridge_status() == {"error": False, "message": ""}


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_targeted_direct_message_reaches_bus(tmp_path: Path) -> None:
    bus = MessageBus()
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=[],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m1",
                "sender": "123@s.whatsapp.net",
                "pn": "+1234567890",
                "pushName": "Alice Chan",
                "content": "hello there",
                "timestamp": 1700000000,
                "isGroup": False,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.channel == "whatsapp"
    assert msg.sender_id == "+1234567890"
    assert msg.chat_id == "123@s.whatsapp.net"
    assert msg.content == "hello there"
    assert msg.metadata["pn"] == "+1234567890"
    assert msg.metadata["sender_phone"] == "+1234567890"
    assert msg.metadata["sender"] == "123@s.whatsapp.net"
    assert msg.metadata["sender_name"] == "Alice Chan"
    assert msg.metadata["capture_only"] is False
    assert msg.metadata["auto_reply_target"] is True
    assert msg.metadata["reply_target_phone"] == "1234567890"
    assert msg.metadata["reply_target_push_name"] == "Alice Chan"
    assert msg.session_key == "whatsapp:1234567890"


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_marks_non_target_direct_message_capture_only(tmp_path: Path) -> None:
    bus = MessageBus()
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+10999999999"], groups=None)
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m1-blocked",
                "sender": "123@s.whatsapp.net",
                "pn": "+1234567890",
                "pushName": "Alice Chan",
                "content": "hello there",
                "timestamp": 1700000000,
                "isGroup": False,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.content == "hello there"
    assert msg.metadata["capture_only"] is True
    assert msg.metadata["auto_reply_target"] is False
    assert msg.metadata["reply_target_phone"] == ""


@pytest.mark.asyncio
async def test_whatsapp_history_batch_only_publishes_target_direct_entries(tmp_path: Path) -> None:
    bus = MessageBus()
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
        workspace=tmp_path,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "history_sync",
                "isLatest": True,
                "messages": [
                    {
                        "id": "h1",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "pushName": "Alice Chan",
                        "content": "older inbound",
                        "timestamp": 1700000000,
                        "fromMe": False,
                        "isGroup": False,
                    },
                    {
                        "id": "h2",
                        "sender": "123@s.whatsapp.net",
                        "pn": "+1234567890",
                        "content": "older outbound",
                        "timestamp": 1700000001,
                        "fromMe": True,
                        "isGroup": False,
                    },
                    {
                        "id": "skip-group",
                        "sender": "1203630@g.us",
                        "pn": "",
                        "content": "group msg",
                        "timestamp": 1700000002,
                        "fromMe": False,
                        "isGroup": True,
                    },
                    {
                        "id": "skip-non-target",
                        "sender": "999@s.whatsapp.net",
                        "pn": "+19999999999",
                        "content": "not targeted",
                        "timestamp": 1700000003,
                        "fromMe": False,
                        "isGroup": False,
                    },
                ],
            }
        )
    )

    batch = await bus.consume_history()
    assert batch.channel == "whatsapp"
    assert batch.metadata["source"] == "history_sync"
    assert len(batch.entries) == 2
    assert [entry["session_key"] for entry in batch.entries] == ["whatsapp:1234567890", "whatsapp:1234567890"]
    assert [entry["from_me"] for entry in batch.entries] == [False, True]
    targets = load_reply_targets(targets_file)
    assert targets["direct_reply_targets"][0]["chat_id"] == "123@s.whatsapp.net"
    assert targets["direct_reply_targets"][0]["push_name"] == "Alice Chan"


@pytest.mark.asyncio
async def test_whatsapp_history_batch_publishes_empty_terminal_request_batch(tmp_path: Path) -> None:
    bus = MessageBus()
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
        workspace=tmp_path,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "web_scrape",
                "requestId": "req-terminal",
                "isLatest": True,
                "messages": [],
            }
        )
    )

    batch = await asyncio.wait_for(bus.consume_history(), timeout=1)
    assert batch.channel == "whatsapp"
    assert batch.entries == []
    assert batch.metadata["source"] == "web_scrape"
    assert batch.metadata["request_id"] == "req-terminal"
    assert batch.metadata["isLatest"] is True


@pytest.mark.asyncio
async def test_whatsapp_falls_back_to_phone_jid_when_pn_is_missing() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, delivery_mode="send", allow_from=["85212345678"], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m1b",
                "sender": "85212345678@s.whatsapp.net",
                "pn": "",
                "content": "hello again",
                "timestamp": 1700000001,
                "isGroup": False,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "85212345678"
    assert msg.chat_id == "85212345678@s.whatsapp.net"
    assert msg.metadata["pn"] == "85212345678"
    assert msg.session_key == "whatsapp:85212345678"


@pytest.mark.asyncio
async def test_whatsapp_self_chat_bypasses_allowlist_and_marks_capture_only() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, delivery_mode="send", allow_from=[], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "self1",
                "sender": "85212345678@s.whatsapp.net",
                "pn": "+85212345678",
                "pushName": "Me",
                "content": "note to self",
                "timestamp": 1700000010,
                "isGroup": False,
                "isSelfChat": True,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "+85212345678"
    assert msg.chat_id == "85212345678@s.whatsapp.net"
    assert msg.metadata["is_self_chat"] is True
    assert msg.metadata["capture_only"] is True
    assert msg.session_key == "whatsapp:85212345678"


@pytest.mark.asyncio
async def test_whatsapp_deleted_direct_message_reaches_bus_as_capture_only() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, delivery_mode="send", allow_from=["+85212345678"], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "deleted",
                "deletedMessageId": "wa-msg-1",
                "sender": "85212345678@s.whatsapp.net",
                "pn": "+85212345678",
                "timestamp": 1700000100,
                "isGroup": False,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.content == ""
    assert msg.metadata["event_type"] == "message_deleted"
    assert msg.metadata["deleted_message_id"] == "wa-msg-1"
    assert msg.metadata["capture_only"] is True
    assert msg.session_key == "whatsapp:85212345678"


@pytest.mark.asyncio
async def test_whatsapp_denies_unlisted_sender() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, delivery_mode="draft", allow_from=["+1234567890"], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m2",
                "sender": "999@s.whatsapp.net",
                "pn": "+19999999999",
                "content": "blocked",
                "timestamp": 1700000000,
                "isGroup": False,
            }
        )
    )

    assert bus.inbound_size == 0


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_ignores_group_messages() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, delivery_mode="draft", allow_from=["+1234567890"], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m3",
                "sender": "group-1@g.us",
                "pn": "",
                "content": "group",
                "timestamp": 1700000000,
                "isGroup": True,
            }
        )
    )

    assert bus.inbound_size == 0


@pytest.mark.asyncio
async def test_whatsapp_group_message_matching_reply_target_json_reaches_bus(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(
        targets_file,
        individuals=None,
        groups=[("Family Group", "+85212345678")],
    )
    observe_group_identification(
        targets_file,
        group_name="Family Group",
        member_phone="+85212345678",
        group_id="1203630group@g.us",
        member_id="alice@lid",
        member_label="Alice",
    )

    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="send",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "g1",
                "sender": "1203630group@g.us",
                "pn": "",
                "participant": "alice@lid",
                "participantPn": "+85212345678",
                "groupId": "1203630group@g.us",
                "groupName": "Family Group",
                "pushName": "Alice",
                "content": "hello group",
                "timestamp": 1700000002,
                "isGroup": True,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "alice@lid"
    assert msg.chat_id == "1203630group@g.us"
    assert msg.metadata["pn"] == "+85212345678"
    assert msg.metadata["sender_phone"] == "+85212345678"
    assert msg.metadata["sender_name"] == "Alice"
    assert msg.metadata["group_id"] == "1203630group@g.us"
    assert msg.metadata["group_name"] == "Family Group"
    assert msg.session_key == "whatsapp:1203630group@g.us:85212345678"


@pytest.mark.asyncio
async def test_whatsapp_group_message_denies_unlisted_member(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(
        targets_file,
        individuals=None,
        groups=[("Family Group", "+85212345678")],
    )

    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="send",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "g2",
                "sender": "1203630group@g.us",
                "pn": "",
                "participant": "mallory@lid",
                "participantPn": "+85200000000",
                "groupId": "1203630group@g.us",
                "groupName": "Family Group",
                "content": "not allowed",
                "timestamp": 1700000003,
                "isGroup": True,
            }
        )
    )

    assert bus.inbound_size == 0


@pytest.mark.asyncio
async def test_whatsapp_group_message_bootstraps_from_group_name_and_phone(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(
        targets_file,
        individuals=None,
        groups=[("Family Group", "+85212345678")],
    )

    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="send",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        ),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "g3",
                "sender": "1203630group@g.us",
                "pn": "",
                "participant": "alice@lid",
                "participantPn": "+85212345678",
                "groupId": "1203630group@g.us",
                "groupName": "Family Group",
                "content": "bootstrap me",
                "timestamp": 1700000004,
                "isGroup": True,
            }
        )
    )

    msg = await bus.consume_inbound()
    assert msg.sender_id == "alice@lid"
    assert msg.chat_id == "1203630group@g.us"
    targets = load_reply_targets(targets_file)
    assert targets["group_reply_targets"][0]["group_id"] == "1203630group@g.us"
    assert targets["group_reply_targets"][0]["member_id"] == "alice@lid"
