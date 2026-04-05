import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.bus.events import HistoryImportResult, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp import WhatsAppChannel
from nanobot.channels.whatsapp_contacts import WhatsAppContact, save_contacts
from nanobot.channels.whatsapp_reply_targets import (
    load_reply_targets,
    observe_direct_identification,
    observe_group_identification,
    rewrite_from_self_instruction,
    save_reply_targets,
)
from nanobot.config.schema import Config, WhatsAppConfig


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


def _make_channel(config: WhatsAppConfig | None = None) -> WhatsAppChannel:
    return WhatsAppChannel(
        config or WhatsAppConfig(
            enabled=True,
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file="",
        ),
        MessageBus(),
    )


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
                    "contactsFile": "~/contacts.json",
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
    assert config.channels.whatsapp.contacts_file == "~/contacts.json"
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
async def test_whatsapp_draft_mode_emits_prepare_draft_command_with_reply_target(tmp_path: Path) -> None:
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
                "searchTerms": ["1234567890", "123", "Alice Chan"],
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
async def test_whatsapp_draft_mode_emits_prepare_draft_command_for_phone_only_target(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
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
                "searchTerms": ["1234567890", "alice"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_uses_contact_label_as_search_fallback(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    contacts_file = tmp_path / "contacts.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    save_contacts(str(contacts_file), [WhatsAppContact(phone="+1234567890", label="Alice Wong", enabled=True)])
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file=str(contacts_file),
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

    assert json.loads(ws.sent[0])["target"]["searchTerms"] == ["1234567890", "alice", "Alice Wong"]


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_ignores_reply_target_label_in_cdp_search_terms(tmp_path: Path) -> None:
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

    assert json.loads(ws.sent[0])["target"]["searchTerms"] == ["1234567890", "123", "Alice Chan"]


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_requests_web_scrape_for_all_enabled_targets(tmp_path: Path) -> None:
    targets_file = tmp_path / "reply_targets.json"
    contacts_file = tmp_path / "contacts.json"
    rewrite_from_self_instruction(targets_file, individuals=["+1234567890"], groups=None)
    observe_direct_identification(
        targets_file,
        phone="+1234567890",
        chat_id="123@s.whatsapp.net",
        sender_id="123@s.whatsapp.net",
        push_name="Alice Chan",
    )
    save_contacts(str(contacts_file), [WhatsAppContact(phone="+1234567890", label="Alice Wong", enabled=True)])
    channel = _make_channel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+1234567890"],
            contacts_file=str(contacts_file),
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="",
            content="",
            metadata={"_internal_command": "sync_direct_history"},
        )
    )

    channel._replay_cached_history.assert_awaited_once()
    assert [json.loads(item) for item in ws.sent] == [
        {
            "type": "scrape_direct_history",
            "targets": [
                {
                    "chatId": "123@s.whatsapp.net",
                    "phone": "1234567890",
                    "searchTerms": ["1234567890", "123", "Alice Chan", "Alice Wong"],
                },
            ],
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_ignores_reply_target_label_in_search_terms(tmp_path: Path) -> None:
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
            contacts_file="",
            group_members_file="",
            reply_targets_file=str(targets_file),
        )
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True
    channel._replay_cached_history = AsyncMock()

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="",
            content="",
            metadata={"_internal_command": "sync_direct_history"},
        )
    )

    assert [json.loads(item) for item in ws.sent] == [
        {
            "type": "scrape_direct_history",
            "targets": [
                {
                    "chatId": "123@s.whatsapp.net",
                    "phone": "1234567890",
                    "searchTerms": ["1234567890", "123", "Alice Chan"],
                },
            ],
        }
    ]


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
            }
        )
    )
    await channel.bus.publish_history_result(
        HistoryImportResult(
            channel="whatsapp",
            matched_entries=2,
            imported_entries=1,
            phones=["1234567890"],
            metadata={"request_id": request_id},
        )
    )

    result = await asyncio.wait_for(task, timeout=1)
    assert result["status"] == "history_scraped"
    assert result["matched_entries"] == 2
    assert result["imported_entries"] == 1


@pytest.mark.asyncio
async def test_whatsapp_sync_direct_history_scopes_web_scrape_to_requested_phones(tmp_path: Path) -> None:
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

    await channel.send(
        OutboundMessage(
            channel="whatsapp",
            chat_id="",
            content="",
            metadata={
                "_internal_command": "sync_direct_history",
                "_target_phones": ["1234567890"],
            },
        )
    )

    channel._replay_cached_history.assert_awaited_once_with(["1234567890"])
    assert [json.loads(item) for item in ws.sent] == [
        {
            "type": "scrape_direct_history",
            "targets": [
                {
                    "chatId": "123@s.whatsapp.net",
                    "phone": "1234567890",
                    "searchTerms": ["1234567890", "123", "Alice Chan"],
                },
            ],
        }
    ]


@pytest.mark.asyncio
async def test_whatsapp_status_connected_queues_direct_history_sync() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, allow_from=["+1234567890"], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(json.dumps({"type": "status", "status": "connected"}))

    msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
    assert msg.channel == "whatsapp"
    assert msg.metadata["_internal_command"] == "sync_direct_history"
    assert "_target_phones" not in msg.metadata


@pytest.mark.asyncio
async def test_whatsapp_draft_mode_targeted_direct_message_reaches_bus(tmp_path: Path) -> None:
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
