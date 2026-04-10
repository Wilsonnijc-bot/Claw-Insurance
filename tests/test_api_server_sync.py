import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.api.server import ApiServer, CDP_DRAFT_DISABLED_DETAIL
from nanobot.bus.queue import MessageBus
from nanobot.channels.whatsapp import WhatsAppChannel
from nanobot.config.schema import WhatsAppConfig
from nanobot.session.manager import SessionManager


class _FakeSessionManager:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def get_session_meta_path(self, _key: str) -> Path:
        return self.base_dir / "missing-meta.json"


class _JSONRequest:
    def __init__(self, *, match_info: dict[str, str] | None = None, body: dict | None = None, query: dict | None = None) -> None:
        self.match_info = match_info or {}
        self.query = query or {}
        self._body = body or {}

    async def json(self) -> dict:
        return self._body


class _FakeWhatsApp:
    def __init__(self, result: dict[str, object] | None = None) -> None:
        self.sync_calls = 0
        self.result = result or {
            "status": "history_scraped",
            "matched_entries": 3,
            "imported_entries": 0,
            "verified_entries": 3,
            "verified_phones": ["1234567890"],
            "backend_success": True,
            "request_id": "req-123",
        }

    async def sync_direct_history(self, phones: list[str]) -> dict[str, object]:
        self.sync_calls += 1
        assert phones == ["1234567890"]
        return dict(self.result)


def _draft_cdp_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(
                reply_targets_file=str(tmp_path / "reply_targets.json"),
                delivery_mode="draft",
                web_browser_mode="cdp",
            )
        )
    )


@pytest.mark.asyncio
async def test_api_sync_uses_actual_history_sync_result_without_any_browser_gate(tmp_path: Path) -> None:
    whatsapp = _FakeWhatsApp()
    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(tmp_path / "reply_targets.json"))
        )
    )
    channel_manager = SimpleNamespace(
        get_channel=lambda name: whatsapp if name == "whatsapp" else None,
        enabled_channels=[],
    )
    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=_FakeSessionManager(tmp_path),
        agent=None,
        channel_manager=channel_manager,
    )
    server._append_journal = AsyncMock()

    response = await server._handle_sync(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert response.status == 200
    assert json.loads(response.text) == {
        "status": "history_scraped",
        "phone": "1234567890",
        "matchedEntries": 3,
        "importedEntries": 0,
        "verifiedEntries": 3,
        "verifiedPhones": ["1234567890"],
        "backendSuccess": True,
        "requestId": "req-123",
    }
    assert whatsapp.sync_calls == 1
    server._append_journal.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_sync_returns_success_when_channel_reports_jsonl_confirmed_backend_success(tmp_path: Path) -> None:
    whatsapp = _FakeWhatsApp(
        {
            "status": "history_scraped",
            "matched_entries": 0,
            "imported_entries": 0,
            "verified_entries": 1,
            "verified_phones": ["1234567890"],
            "backend_success": True,
            "request_id": "req-jsonl",
        }
    )
    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(tmp_path / "reply_targets.json"))
        )
    )
    channel_manager = SimpleNamespace(
        get_channel=lambda name: whatsapp if name == "whatsapp" else None,
        enabled_channels=[],
    )
    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=_FakeSessionManager(tmp_path),
        agent=None,
        channel_manager=channel_manager,
    )
    server._append_journal = AsyncMock()

    response = await server._handle_sync(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert response.status == 200
    assert json.loads(response.text) == {
        "status": "history_scraped",
        "phone": "1234567890",
        "matchedEntries": 0,
        "importedEntries": 0,
        "verifiedEntries": 1,
        "verifiedPhones": ["1234567890"],
        "backendSuccess": True,
        "requestId": "req-jsonl",
    }
    server._append_journal.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_sync_history_scraped_without_verified_session_stays_backend_unsuccessful(tmp_path: Path) -> None:
    whatsapp = _FakeWhatsApp(
        {
            "status": "history_scraped",
            "matched_entries": 3,
            "imported_entries": 2,
            "verified_entries": 0,
            "verified_phones": [],
            "backend_success": False,
            "request_id": "req-456",
        }
    )
    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(tmp_path / "reply_targets.json"))
        )
    )
    channel_manager = SimpleNamespace(
        get_channel=lambda name: whatsapp if name == "whatsapp" else None,
        enabled_channels=[],
    )
    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=_FakeSessionManager(tmp_path),
        agent=None,
        channel_manager=channel_manager,
    )
    server._append_journal = AsyncMock()

    response = await server._handle_sync(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert response.status == 200
    assert json.loads(response.text) == {
        "status": "history_scraped",
        "phone": "1234567890",
        "matchedEntries": 3,
        "importedEntries": 2,
        "verifiedEntries": 0,
        "verifiedPhones": [],
        "backendSuccess": False,
        "requestId": "req-456",
    }
    server._append_journal.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_rejects_cdp_draft_delivery_before_persist(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_draft_cdp_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    response = await server._handle_send_message(
        _JSONRequest(match_info={"phone": "1234567890"}, body={"content": "hello"})
    )

    assert response.status == 409
    assert json.loads(response.text) == {
        "error": CDP_DRAFT_DISABLED_DETAIL,
        "code": "draft_delivery_disabled",
    }
    assert not (tmp_path / "sessions" / "whatsapp__1234567890" / "session.jsonl").exists()


@pytest.mark.asyncio
async def test_send_message_persists_once_when_baileys_upsert_echo_arrives(tmp_path: Path) -> None:
    reply_targets_path = tmp_path / "reply_targets.json"
    reply_targets_path.write_text(json.dumps({
        "version": 1,
        "updated_at": "",
        "source": "test",
        "direct_reply_targets": [
            {
                "phone": "1234567890",
                "enabled": True,
                "auto_draft": False,
                "chat_id": "1234567890@s.whatsapp.net",
                "sender_id": "1234567890@s.whatsapp.net",
                "push_name": "Alice",
            }
        ],
        "group_reply_targets": [],
    }), encoding="utf-8")

    whatsapp_config = WhatsAppConfig(
        enabled=True,
        delivery_mode="send",
        allow_from=["1234567890"],
        contacts_file="",
        group_members_file="",
        reply_targets_file=str(reply_targets_path),
    )
    bus = MessageBus()
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=SimpleNamespace(channels=SimpleNamespace(whatsapp=whatsapp_config)),
        bus=bus,
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )
    server._append_journal = AsyncMock()
    channel = WhatsAppChannel(whatsapp_config, bus, workspace=tmp_path)

    response = await server._handle_send_message(
        _JSONRequest(match_info={"phone": "1234567890"}, body={"content": "hello"})
    )

    assert response.status == 200
    session_key = "whatsapp:1234567890"
    session = session_manager.read_persisted(session_key)
    assert len(session.messages) == 1
    assert session.messages[0]["role"] == "me"
    assert session.messages[0]["content"] == "hello"
    original_message_id = session.messages[0]["message_id"]

    await channel._handle_bridge_message(
        json.dumps(
            {
                "type": "history",
                "source": "upsert",
                "messages": [
                    {
                        "id": "BAILEYS_REAL_ID",
                        "sender": "1234567890@s.whatsapp.net",
                        "pn": "1234567890",
                        "content": "hello",
                        "timestamp": 1700000000,
                        "fromMe": True,
                        "isGroup": False,
                    }
                ],
            }
        )
    )

    assert bus.history_size == 0
    session = session_manager.read_persisted(session_key)
    assert len(session.messages) == 1
    assert [msg["message_id"] for msg in session.messages] == [original_message_id]
    assert [msg["content"] for msg in session.messages] == ["hello"]


@pytest.mark.asyncio
async def test_ai_send_rejects_cdp_draft_delivery_before_persist(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_draft_cdp_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    response = await server._handle_ai_send(
        _JSONRequest(match_info={"phone": "1234567890"}, body={"content": "approved"})
    )

    assert response.status == 409
    assert json.loads(response.text) == {
        "error": CDP_DRAFT_DISABLED_DETAIL,
        "code": "draft_delivery_disabled",
    }
    assert not (tmp_path / "sessions" / "whatsapp__1234567890" / "session.jsonl").exists()


@pytest.mark.asyncio
async def test_broadcast_rejects_cdp_draft_delivery_before_persist(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_draft_cdp_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    response = await server._handle_broadcast(
        _JSONRequest(body={"phones": ["1234567890"], "content": "hello everyone"})
    )

    assert response.status == 409
    assert json.loads(response.text) == {
        "error": CDP_DRAFT_DISABLED_DETAIL,
        "code": "draft_delivery_disabled",
    }
    assert not (tmp_path / "sessions" / "whatsapp__1234567890" / "session.jsonl").exists()


@pytest.mark.asyncio
async def test_delete_client_removes_session_bundle_and_reply_target(tmp_path: Path) -> None:
    reply_targets_path = tmp_path / "data" / "reply_targets.json"
    reply_targets_path.parent.mkdir(parents=True, exist_ok=True)
    reply_targets_path.write_text(json.dumps({
        "version": 1,
        "updated_at": "",
        "source": "test",
        "direct_reply_targets": [
            {
                "phone": "1234567890",
                "enabled": True,
                "auto_draft": True,
                "chat_id": "1234567890@s.whatsapp.net",
                "sender_id": "1234567890@s.whatsapp.net",
                "push_name": "Alice",
            }
        ],
        "group_reply_targets": [],
    }), encoding="utf-8")

    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(reply_targets_path))
        )
    )
    session_manager = SessionManager(tmp_path)
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "hello", message_id="msg-1")
    session_manager.save(session)

    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    response = await server._handle_delete_client(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert response.status == 200
    assert json.loads(response.text) == {"status": "deleted", "phone": "1234567890"}

    bundle_dir = tmp_path / "sessions" / "whatsapp__1234567890"
    assert not (bundle_dir / "session.jsonl").exists()
    assert not (bundle_dir / "meta.json").exists()
    assert not bundle_dir.exists()
    assert "whatsapp:1234567890" not in session_manager._cache
    assert json.loads(reply_targets_path.read_text(encoding="utf-8"))["direct_reply_targets"] == []

    second = await server._handle_delete_client(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert second.status == 200
    assert json.loads(second.text) == {"status": "deleted", "phone": "1234567890"}

    clients_response = await server._handle_get_clients(SimpleNamespace())
    assert json.loads(clients_response.text) == {"clients": []}


@pytest.mark.asyncio
async def test_get_messages_is_explicitly_no_store(tmp_path: Path) -> None:
    reply_targets_path = tmp_path / "data" / "reply_targets.json"
    reply_targets_path.parent.mkdir(parents=True, exist_ok=True)
    reply_targets_path.write_text(json.dumps({
        "version": 1,
        "updated_at": "",
        "source": "test",
        "direct_reply_targets": [],
        "group_reply_targets": [],
    }), encoding="utf-8")

    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(reply_targets_path))
        )
    )
    session_manager = SessionManager(tmp_path)
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "hello", message_id="msg-1")
    session_manager.save(session)

    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    response = await server._handle_get_messages(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert response.status == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
    assert json.loads(response.text)["messages"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_get_messages_html_format_renders_html_from_session(tmp_path: Path) -> None:
    reply_targets_path = tmp_path / "data" / "reply_targets.json"
    reply_targets_path.parent.mkdir(parents=True, exist_ok=True)
    reply_targets_path.write_text(json.dumps({
        "version": 1,
        "updated_at": "",
        "source": "test",
        "direct_reply_targets": [
            {
                "phone": "1234567890",
                "enabled": True,
                "auto_draft": False,
                "push_name": "Alice",
            }
        ],
        "group_reply_targets": [],
    }), encoding="utf-8")

    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(reply_targets_path))
        )
    )
    session_manager = SessionManager(tmp_path)
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "hello from session", message_id="msg-1")
    session.add_message("assistant", "reply from agent", message_id="msg-2")
    session_manager.save(session)

    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    response = await server._handle_get_messages(
        SimpleNamespace(match_info={"phone": "1234567890"}, query={"format": "html"})
    )

    assert response.status == 200
    assert response.content_type == "text/html"
    assert response.headers["Cache-Control"] == "no-store"
    assert "hello from session" in response.text
    assert "reply from agent" in response.text


@pytest.mark.asyncio
async def test_get_messages_exposes_imported_reply_with_quote_fields_and_renders_quote_html(tmp_path: Path) -> None:
    reply_targets_path = tmp_path / "data" / "reply_targets.json"
    reply_targets_path.parent.mkdir(parents=True, exist_ok=True)
    reply_targets_path.write_text(json.dumps({
        "version": 1,
        "updated_at": "",
        "source": "test",
        "direct_reply_targets": [
            {
                "phone": "1234567890",
                "enabled": True,
                "auto_draft": False,
                "push_name": "Alice",
            }
        ],
        "group_reply_targets": [],
    }), encoding="utf-8")

    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(reply_targets_path))
        )
    )
    session_manager = SessionManager(tmp_path)
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message(
        "client",
        "Actual reply text",
        message_id="msg-quoted-1",
        message_type="imported_client_reply_with_quote",
        reply_text="Actual reply text",
        quoted_text="Earlier outbound text",
        quoted_message_id="msg-prev-1",
    )
    session_manager.save(session)

    server = ApiServer(
        config=config,
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    json_response = await server._handle_get_messages(SimpleNamespace(match_info={"phone": "1234567890"}))
    payload = json.loads(json_response.text)
    assert payload["messages"][0]["content"] == "Actual reply text"
    assert payload["messages"][0]["messageType"] == "imported_client_reply_with_quote"
    assert payload["messages"][0]["replyText"] == "Actual reply text"
    assert payload["messages"][0]["quotedText"] == "Earlier outbound text"
    assert payload["messages"][0]["quotedMessageId"] == "msg-prev-1"

    html_response = await server._handle_get_messages(
        SimpleNamespace(match_info={"phone": "1234567890"}, query={"format": "html"})
    )

    assert html_response.status == 200
    assert "quoted-block" in html_response.text
    assert "Earlier outbound text" in html_response.text
    assert "Actual reply text" in html_response.text
    assert ">你</div>" not in html_response.text
