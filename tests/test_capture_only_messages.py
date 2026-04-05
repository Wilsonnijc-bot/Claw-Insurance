from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import HistoryImportResult, InboundHistoryBatch, InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp import WhatsAppChannel
from nanobot.channels.whatsapp_contacts import load_contacts
from nanobot.channels.whatsapp_group_members import load_group_members
from nanobot.channels.whatsapp_reply_targets import load_reply_targets, rewrite_from_self_instruction
from nanobot.config.schema import ChannelsConfig, WhatsAppConfig
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path, channels_config: ChannelsConfig | None = None) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        memory_window=10,
        channels_config=channels_config,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


@pytest.mark.asyncio
async def test_capture_only_message_is_saved_without_reply_or_llm_call(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for capture_only"))

    msg = InboundMessage(
        channel="whatsapp",
        sender_id="85212345678",
        chat_id="85212345678@s.whatsapp.net",
        content="self note",
        metadata={"capture_only": True, "is_self_chat": True},
        session_key_override="whatsapp:85212345678",
    )

    response = await loop._process_message(msg)
    assert response is None
    loop.provider.chat.assert_not_called()

    session = loop.sessions.get_or_create("whatsapp:85212345678")
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "me"
    assert session.messages[0]["content"] == "self note"


@pytest.mark.asyncio
async def test_capture_only_self_command_updates_whatsapp_routing_files(tmp_path: Path) -> None:
    contacts_file = str(tmp_path / "contacts.json")
    groups_file = str(tmp_path / "groups.csv")
    targets_file = str(tmp_path / "data" / "whatsapp_reply_targets.json")
    channels_config = ChannelsConfig(
        whatsapp=WhatsAppConfig(
            enabled=True,
            contacts_file=contacts_file,
            group_members_file=groups_file,
            reply_targets_file=targets_file,
        )
    )
    loop = _make_loop(tmp_path, channels_config=channels_config)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for capture_only"))

    cmd = """
    #chatbot reply to individuals#
    +85212345678
    #chatbot reply to individuals#
    #chatbot reply to groups#
    Insurance sales, +852 6943 2591
    #chatbot reply to groups#
    """
    msg = InboundMessage(
        channel="whatsapp",
        sender_id="85212345678",
        chat_id="85212345678@s.whatsapp.net",
        content=cmd,
        metadata={"capture_only": True, "is_self_chat": True},
        session_key_override="whatsapp:85212345678",
    )

    response = await loop._process_message(msg)
    assert response is None
    loop.provider.chat.assert_not_called()
    sync_cmd = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1)
    assert sync_cmd.channel == "whatsapp"
    assert sync_cmd.metadata["_internal_command"] == "sync_direct_history"
    assert sync_cmd.metadata["_target_phones"] == ["85212345678"]

    contacts = load_contacts(contacts_file)
    rows = load_group_members(groups_file)
    targets = load_reply_targets(Path(targets_file))
    assert [c.phone for c in contacts] == ["85212345678"]
    assert len(rows) == 1
    assert rows[0].group_name == "Insurance sales"
    assert rows[0].member_pn == "85269432591"
    assert [row["phone"] for row in targets["direct_reply_targets"]] == ["85212345678"]
    assert len(targets["group_reply_targets"]) == 1
    assert targets["group_reply_targets"][0]["group_name"] == "Insurance sales"


@pytest.mark.asyncio
async def test_deleted_message_event_marks_existing_history_without_removing_content(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for delete marker"))

    original = InboundMessage(
        channel="whatsapp",
        sender_id="85212345678",
        chat_id="85212345678@s.whatsapp.net",
        content="message before delete",
        metadata={"capture_only": True, "message_id": "wa-msg-1"},
        session_key_override="whatsapp:85212345678",
    )
    await loop._process_message(original)

    deleted = InboundMessage(
        channel="whatsapp",
        sender_id="85212345678",
        chat_id="85212345678@s.whatsapp.net",
        content="",
        metadata={
            "capture_only": True,
            "event_type": "message_deleted",
            "deleted_message_id": "wa-msg-1",
            "deleted_by_sender": True,
            "deleted_at": "2026-03-11T18:12:00",
            "sender": "85212345678@s.whatsapp.net",
        },
        session_key_override="whatsapp:85212345678",
    )

    response = await loop._process_message(deleted)
    assert response is None
    loop.provider.chat.assert_not_called()

    session = loop.sessions.get_or_create("whatsapp:85212345678")
    assert len(session.messages) == 1
    assert session.messages[0]["content"] == "message before delete"
    assert session.messages[0]["message_id"] == "wa-msg-1"
    assert session.messages[0]["deleted_by_sender"] is True
    assert session.messages[0]["deleted_at"] == "2026-03-11T18:12:00"
    assert session.metadata["deleted_messages"][0]["message_id"] == "wa-msg-1"
    assert session.metadata["deleted_messages"][0]["matched_message"] is True


@pytest.mark.asyncio
async def test_dispatch_history_publishes_import_result_for_request_scoped_batch(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    observer = loop.bus.add_history_result_observer()
    batch = InboundHistoryBatch(
        channel="whatsapp",
        entries=[
            {
                "session_key": "whatsapp:15550001111",
                "chat_id": "15550001111@s.whatsapp.net",
                "phone": "15550001111",
                "sender": "15550001111@s.whatsapp.net",
                "sender_id": "15550001111",
                "content": "Hi",
                "message_id": "wa-hist-1",
                "timestamp": 1700000000,
                "from_me": False,
                "push_name": "Alice",
            }
        ],
        metadata={"request_id": "req-123"},
    )

    try:
        await loop._dispatch_history(batch)
        result = await asyncio.wait_for(observer.get(), timeout=1)
    finally:
        loop.bus.remove_history_result_observer(observer)

    assert isinstance(result, HistoryImportResult)
    assert result.channel == "whatsapp"
    assert result.metadata["request_id"] == "req-123"
    assert result.matched_entries == 1
    assert result.imported_entries == 1
    assert result.phones == ["15550001111"]


@pytest.mark.asyncio
async def test_normal_whatsapp_turn_persists_inbound_message_id_for_future_delete_markers(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.provider.chat = AsyncMock(return_value=LLMResponse(content="reply"))

    msg = InboundMessage(
        channel="whatsapp",
        sender_id="85212345678",
        chat_id="85212345678@s.whatsapp.net",
        content="hello there",
        metadata={"message_id": "wa-msg-2"},
        session_key_override="whatsapp:85212345678",
    )

    response = await loop._process_message(msg)

    assert response is not None
    session = loop.sessions.get_or_create("whatsapp:85212345678")
    assert session.messages[0]["role"] == "client"
    assert session.messages[0]["message_id"] == "wa-msg-2"
    assert session.messages[0]["content"] == "hello there"


@pytest.mark.asyncio
async def test_whatsapp_prompt_uses_full_stored_history_not_only_memory_window(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.provider.chat = AsyncMock(return_value=LLMResponse(content="reply"))

    session = loop.sessions.get_or_create("whatsapp:85212345678")
    session.add_message("user", "older client message 1")
    session.add_message("assistant", "older my message 1")
    session.add_message("user", "older client message 2")
    session.add_message("assistant", "older my message 2")
    session.add_message("user", "older client message 3")
    session.add_message("assistant", "older my message 3")
    loop.sessions.save(session)

    msg = InboundMessage(
        channel="whatsapp",
        sender_id="85212345678",
        chat_id="85212345678@s.whatsapp.net",
        content="new inbound",
        metadata={"message_id": "wa-msg-full-history"},
        session_key_override="whatsapp:85212345678",
    )

    response = await loop._process_message(msg)

    assert response is not None
    sent_messages = loop.provider.chat.await_args.kwargs["messages"]
    assert [item["content"] for item in sent_messages[1:7]] == [
        "older client message 1",
        "older my message 1",
        "older client message 2",
        "older my message 2",
        "older client message 3",
        "older my message 3",
    ]
    assert [item["role"] for item in sent_messages[1:7]] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert any(
        item.get("role") == "user" and "new inbound" in str(item.get("content", ""))
        for item in sent_messages[7:]
    )


@pytest.mark.asyncio
async def test_non_target_whatsapp_draft_message_from_channel_skips_llm_and_reply(tmp_path: Path) -> None:
    targets_file = str(tmp_path / "data" / "whatsapp_reply_targets.json")
    channels_config = ChannelsConfig(
        whatsapp=WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=["+85212345678"],
            contacts_file="",
            group_members_file="",
            reply_targets_file=targets_file,
        )
    )
    loop = _make_loop(tmp_path, channels_config=channels_config)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for non-target draft capture"))
    channel = WhatsAppChannel(channels_config.whatsapp, loop.bus, workspace=tmp_path)

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "wa-non-target-1",
                "sender": "85212345678@s.whatsapp.net",
                "pn": "+85212345678",
                "pushName": "Alice",
                "content": "hello draft mode",
                "timestamp": 1700001000,
                "isGroup": False,
            }
        )
    )

    msg = await loop.bus.consume_inbound()
    assert msg.metadata["capture_only"] is True

    response = await loop._process_message(msg)
    assert response is None
    loop.provider.chat.assert_not_called()


@pytest.mark.asyncio
async def test_whatsapp_history_batch_imports_both_sides_without_llm_and_updates_visible_exports(tmp_path: Path) -> None:
    targets_file = str(tmp_path / "data" / "whatsapp_reply_targets.json")
    target_phone = "+15550001111"
    normalized_phone = "15550001111"
    chat_id = "15550001111@s.whatsapp.net"
    channels_config = ChannelsConfig(
        whatsapp=WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=[target_phone],
            contacts_file="",
            group_members_file="",
            reply_targets_file=targets_file,
        )
    )
    rewrite_payload = {
        "type": "history",
        "source": "history_sync",
        "messages": [
            {
                "id": "wa-hist-1",
                "sender": chat_id,
                "pn": target_phone,
                "pushName": "Alice",
                "content": "Hi",
                "timestamp": 1700000000,
                "fromMe": False,
                "isGroup": False,
            },
            {
                "id": "wa-hist-2",
                "sender": chat_id,
                "pn": target_phone,
                "content": "Hello Alice",
                "timestamp": 1700000001,
                "fromMe": True,
                "isGroup": False,
            },
        ],
    }

    loop = _make_loop(tmp_path, channels_config=channels_config)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for history import"))
    channel = WhatsAppChannel(channels_config.whatsapp, loop.bus, workspace=tmp_path)
    rewrite_from_self_instruction(Path(targets_file), individuals=[target_phone], groups=None)

    await channel._handle_bridge_message(json.dumps(rewrite_payload))
    batch = await loop.bus.consume_history()

    loop._import_history_batch(batch)
    loop.provider.chat.assert_not_called()

    session = loop.sessions.get_or_create(f"whatsapp:{normalized_phone}")
    assert [msg["role"] for msg in session.messages] == ["client", "me"]
    assert [msg["content"] for msg in session.messages] == ["Hi", "Hello Alice"]
    assert [msg["message_id"] for msg in session.messages] == ["wa-hist-1", "wa-hist-2"]

    bundle_meta = loop.sessions.get_session_meta_path(f"whatsapp:{normalized_phone}")
    assert bundle_meta.exists()
    meta = json.loads(bundle_meta.read_text(encoding="utf-8"))
    assert meta["session_file"].endswith("session.jsonl")

    session.metadata["client_label"] = "Alice Chan"
    session.metadata["client_push_name"] = "Alice"
    loop.sessions.save(session)
    meta = json.loads(bundle_meta.read_text(encoding="utf-8"))
    assert meta["client_name"] == "Alice Chan"
    assert meta["client"]["name"] == "Alice Chan"
    assert meta["client"]["push_name"] == "Alice"
    assert meta["metadata"]["client_name"] == "Alice Chan"

    loop._import_history_batch(batch)
    assert len(loop.sessions.get_or_create(f"whatsapp:{normalized_phone}").messages) == 2


@pytest.mark.asyncio
async def test_self_chat_command_replays_cached_history_for_new_reply_targets(tmp_path: Path) -> None:
    targets_file = str(tmp_path / "data" / "whatsapp_reply_targets.json")
    contacts_file = str(tmp_path / "contacts.json")
    groups_file = str(tmp_path / "groups.csv")
    target_phone = "+15550002222"
    normalized_phone = "15550002222"
    target_chat_id = "15550002222@s.whatsapp.net"
    channels_config = ChannelsConfig(
        whatsapp=WhatsAppConfig(
            enabled=True,
            delivery_mode="draft",
            allow_from=[target_phone],
            contacts_file=contacts_file,
            group_members_file=groups_file,
            reply_targets_file=targets_file,
        )
    )
    loop = _make_loop(tmp_path, channels_config=channels_config)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for history replay"))
    channel = WhatsAppChannel(channels_config.whatsapp, loop.bus, workspace=tmp_path)

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "history_sync",
                "messages": [
                    {
                        "id": "wa-cache-1",
                        "sender": target_chat_id,
                        "pn": target_phone,
                        "pushName": "Alice",
                        "content": "Earlier hello",
                        "timestamp": 1700000100,
                        "fromMe": False,
                        "isGroup": False,
                    },
                    {
                        "id": "wa-cache-2",
                        "sender": target_chat_id,
                        "pn": target_phone,
                        "content": "Earlier reply",
                        "timestamp": 1700000101,
                        "fromMe": True,
                        "isGroup": False,
                    },
                ],
            }
        )
    )
    assert loop.bus.history_size == 0

    cmd = """
    #chatbot reply to individuals#
    +15550002222
    #chatbot reply to individuals#
    """
    response = await loop._process_message(
        InboundMessage(
            channel="whatsapp",
            sender_id="85212345678",
            chat_id="85212345678@s.whatsapp.net",
            content=cmd,
            metadata={"capture_only": True, "is_self_chat": True},
            session_key_override="whatsapp:85212345678",
        )
    )
    assert response is None

    sync_cmd = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1)
    assert sync_cmd.metadata["_internal_command"] == "sync_direct_history"
    assert sync_cmd.metadata["_target_phones"] == [normalized_phone]

    await channel.send(sync_cmd)
    batch = await asyncio.wait_for(loop.bus.consume_history(), timeout=1)
    loop._import_history_batch(batch)

    session = loop.sessions.get_or_create(f"whatsapp:{normalized_phone}")
    assert [msg["role"] for msg in session.messages] == ["client", "me"]
    assert [msg["message_id"] for msg in session.messages] == ["wa-cache-1", "wa-cache-2"]
