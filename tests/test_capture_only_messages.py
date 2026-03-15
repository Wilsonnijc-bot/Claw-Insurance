from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp_contacts import load_contacts
from nanobot.channels.whatsapp_group_members import load_group_members
from nanobot.channels.whatsapp_reply_targets import load_reply_targets
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
    assert session.messages[0]["role"] == "user"
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
    assert session.messages[0]["role"] == "user"
    assert session.messages[0]["message_id"] == "wa-msg-2"
    assert session.messages[0]["content"] == "hello there"
