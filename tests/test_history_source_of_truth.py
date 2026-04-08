from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.api.server import ApiServer
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse
from nanobot.session.manager import SessionManager


def _reply_targets_path(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "reply_targets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
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
                "label": "Alice",
            }
        ],
        "group_reply_targets": [],
    }), encoding="utf-8")
    return path


def _make_server(
    tmp_path: Path,
    *,
    session_manager: SessionManager | None = None,
    bus: MessageBus | None = None,
    agent: object | None = None,
) -> ApiServer:
    reply_targets_path = _reply_targets_path(tmp_path)
    config = SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(reply_targets_path))
        )
    )
    return ApiServer(
        config=config,
        bus=bus or MessageBus(),
        session_manager=session_manager or SessionManager(tmp_path),
        agent=agent,
        channel_manager=SimpleNamespace(enabled_channels=[], get_channel=lambda _name: None),
    )


def _make_loop(tmp_path: Path, *, bus: MessageBus, session_manager: SessionManager) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat = AsyncMock(return_value=LLMResponse(content="draft reply"))
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        memory_window=10,
        session_manager=session_manager,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


@pytest.mark.asyncio
async def test_get_messages_reads_only_from_persisted_jsonl(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "persisted hello", message_id="msg-1")
    session_manager.save(session)

    # Mutate cache without saving. The API must ignore this.
    session.add_message("client", "unsaved cache message", message_id="msg-2")

    server = _make_server(tmp_path, session_manager=session_manager)
    response = await server._handle_get_messages(SimpleNamespace(match_info={"phone": "1234567890"}))

    payload = json.loads(response.text)
    assert [item["content"] for item in payload["messages"]] == ["persisted hello"]


@pytest.mark.asyncio
async def test_client_summaries_use_persisted_jsonl_not_unsaved_cache(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "persisted hello", message_id="msg-1")
    session_manager.save(session)

    session.add_message("client", "unsaved cache message", message_id="msg-2")

    server = _make_server(tmp_path, session_manager=session_manager)

    single = await server._handle_get_client(SimpleNamespace(match_info={"phone": "1234567890"}))
    single_payload = json.loads(single.text)
    assert single_payload["lastMessage"] == "persisted hello"
    assert single_payload["messageCount"] == 1

    listing = await server._handle_get_clients(SimpleNamespace())
    clients_payload = json.loads(listing.text)
    assert clients_payload["clients"][0]["lastMessage"] == "persisted hello"
    assert clients_payload["clients"][0]["messageCount"] == 1


@pytest.mark.asyncio
async def test_persisted_history_websocket_notifies_only_after_jsonl_save(tmp_path: Path) -> None:
    bus = MessageBus()
    session_manager = SessionManager(tmp_path)
    loop = _make_loop(tmp_path, bus=bus, session_manager=session_manager)
    loop.provider.chat = AsyncMock(side_effect=AssertionError("LLM should not be called for capture_only"))

    server = _make_server(tmp_path, session_manager=session_manager, bus=bus, agent=loop)
    persisted_path = session_manager.get_session_path("whatsapp:1234567890")
    broadcasts: list[dict[str, object]] = []
    notified = asyncio.Event()

    async def capture_broadcast(event: dict[str, object]) -> None:
        assert persisted_path.exists()
        assert "hello persisted first" in persisted_path.read_text(encoding="utf-8")
        broadcasts.append(event)
        notified.set()

    server._broadcast_ws = AsyncMock(side_effect=capture_broadcast)
    mirror_task = asyncio.create_task(server._mirror_persisted_history())
    await asyncio.sleep(0)

    try:
        await loop._process_message(
            msg=InboundMessage(
                channel="whatsapp",
                sender_id="1234567890",
                chat_id="1234567890@s.whatsapp.net",
                content="hello persisted first",
                metadata={"capture_only": True, "message_id": "wa-1"},
                session_key_override="whatsapp:1234567890",
            )
        )
        await asyncio.wait_for(notified.wait(), timeout=1)
    finally:
        mirror_task.cancel()
        await mirror_task

    assert broadcasts
    assert broadcasts[0]["type"] == "new_message"
    assert broadcasts[0]["phone"] == "1234567890"


@pytest.mark.asyncio
async def test_manual_ai_draft_does_not_write_unsent_content_to_session_jsonl(tmp_path: Path) -> None:
    bus = MessageBus()
    session_manager = SessionManager(tmp_path)
    loop = _make_loop(tmp_path, bus=bus, session_manager=session_manager)
    server = _make_server(tmp_path, session_manager=session_manager, bus=bus, agent=loop)
    server._broadcast_ws = AsyncMock()

    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "Client question", message_id="client-1")
    session_manager.save(session)

    response = await server._handle_ai_draft(SimpleNamespace(match_info={"phone": "1234567890"}))

    assert response.status == 200
    persisted = session_manager.read_persisted("whatsapp:1234567890")
    assert [msg["content"] for msg in persisted.messages if msg.get("role") != "tool"] == ["Client question"]
    assert all(msg.get("content") != "draft reply" for msg in persisted.messages)


@pytest.mark.asyncio
async def test_auto_draft_does_not_write_unsent_content_to_session_jsonl(tmp_path: Path) -> None:
    bus = MessageBus()
    session_manager = SessionManager(tmp_path)
    loop = _make_loop(tmp_path, bus=bus, session_manager=session_manager)
    server = _make_server(tmp_path, session_manager=session_manager, bus=bus, agent=loop)
    server._broadcast_ws = AsyncMock()

    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message("client", "Client question", message_id="client-1")
    session_manager.save(session)

    await server._auto_generate_draft("1234567890", "Client question")

    persisted = session_manager.read_persisted("whatsapp:1234567890")
    assert [msg["content"] for msg in persisted.messages if msg.get("role") != "tool"] == ["Client question"]
    assert all(msg.get("content") != "draft reply" for msg in persisted.messages)


def test_vite_config_has_no_messages_view_fallback() -> None:
    config_path = Path(__file__).resolve().parents[1] / "Insurance frontend" / "vite.config.ts"
    content = config_path.read_text(encoding="utf-8")

    assert "messages-view-fallback" not in content
    assert "renderMessagesDocument" not in content
    assert "FallbackMessage" not in content


def test_frontend_thread_files_do_not_persist_history_to_browser_storage() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    files = [
        repo_root / "Insurance frontend" / "src" / "components" / "MessageCenter" / "MessageThread.tsx",
        repo_root / "Insurance frontend" / "src" / "hooks" / "useNanobot.ts",
        repo_root / "Insurance frontend" / "src" / "services" / "api.ts",
        repo_root / "Insurance frontend" / "src" / "services" / "websocket.ts",
    ]

    for path in files:
        content = path.read_text(encoding="utf-8")
        assert "localStorage" not in content
        assert "sessionStorage" not in content
        assert "indexedDB" not in content
        assert "IndexedDB" not in content
