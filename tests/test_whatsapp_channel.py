import json
from pathlib import Path

import pytest

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp import WhatsAppChannel
from nanobot.channels.whatsapp_group_members import WhatsAppGroupMember, save_group_members
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
                    "webProfileDir": "~/custom-whatsapp-web",
                    "contactsFile": "~/contacts.json",
                    "groupMembersFile": "~/groups.csv",
                    "storageDir": "~/whatsapp-storage",
                    "allowFrom": ["+1234567890"],
                }
            }
        }
    )

    assert config.channels.whatsapp.delivery_mode == "draft"
    assert config.channels.whatsapp.web_profile_dir == "~/custom-whatsapp-web"
    assert config.channels.whatsapp.contacts_file == "~/contacts.json"
    assert config.channels.whatsapp.group_members_file == "~/groups.csv"
    assert config.channels.whatsapp.storage_dir == "~/whatsapp-storage"


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
async def test_whatsapp_draft_mode_emits_prepare_draft_command() -> None:
    channel = _make_channel(
        WhatsAppConfig(enabled=True, delivery_mode="draft", allow_from=["+1234567890"], contacts_file="", group_members_file="")
    )
    ws = _FakeWebSocket()
    channel._ws = ws
    channel._connected = True

    await channel.send(OutboundMessage(channel="whatsapp", chat_id="123@s.whatsapp.net", content="draft me"))

    assert [json.loads(item) for item in ws.sent] == [
        {"type": "prepare_draft", "to": "123@s.whatsapp.net", "text": "draft me"}
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
async def test_whatsapp_allowed_direct_message_reaches_bus() -> None:
    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(enabled=True, delivery_mode="draft", allow_from=["+1234567890"], contacts_file="", group_members_file=""),
        bus,
    )

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "message",
                "id": "m1",
                "sender": "123@s.whatsapp.net",
                "pn": "+1234567890",
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
    assert msg.metadata["sender"] == "123@s.whatsapp.net"
    assert msg.session_key == "whatsapp:1234567890"


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
async def test_whatsapp_group_message_matching_csv_reaches_bus(tmp_path: Path) -> None:
    groups_file = tmp_path / "whatsapp_groups.csv"
    save_group_members(
        str(groups_file),
        [
            WhatsAppGroupMember(
                group_id="1203630group@g.us",
                group_name="Family Group",
                member_id="alice@lid",
                member_pn="+85212345678",
                member_label="Alice",
            )
        ],
    )

    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="send",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file=str(groups_file),
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
    assert msg.metadata["group_id"] == "1203630group@g.us"
    assert msg.metadata["group_name"] == "Family Group"
    assert msg.session_key == "whatsapp:1203630group@g.us:85212345678"


@pytest.mark.asyncio
async def test_whatsapp_group_message_denies_unlisted_member(tmp_path: Path) -> None:
    groups_file = tmp_path / "whatsapp_groups.csv"
    save_group_members(
        str(groups_file),
        [
            WhatsAppGroupMember(
                group_id="1203630group@g.us",
                group_name="Family Group",
                member_id="alice@lid",
                member_pn="+85212345678",
                member_label="Alice",
            )
        ],
    )

    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="send",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file=str(groups_file),
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
    groups_file = tmp_path / "whatsapp_groups.csv"
    save_group_members(
        str(groups_file),
        [
            WhatsAppGroupMember(
                group_id="",
                group_name="Family Group",
                member_id="",
                member_pn="+85212345678",
                member_label="Alice",
            )
        ],
    )

    bus = MessageBus()
    channel = WhatsAppChannel(
        WhatsAppConfig(
            enabled=True,
            delivery_mode="send",
            allow_from=["+1234567890"],
            contacts_file="",
            group_members_file=str(groups_file),
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
    assert "1203630group@g.us,Family Group,alice@lid,+85212345678,Alice,true" in groups_file.read_text()
