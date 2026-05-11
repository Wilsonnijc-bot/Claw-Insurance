from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.loop import AgentLoop
from nanobot.api.launcher import LauncherServer
from nanobot.api.server import ApiServer
from nanobot.bus.queue import MessageBus
from nanobot.session.manager import (
    LEGACY_OFFLINE_MEETING_TRANSCRIPTS_KEY,
    OFFLINE_MEETING_NOTE_INDEX_KEY,
    OFFLINE_MEETING_NOTE_TYPE,
    Session,
    SessionManager,
)


class _FakeMultipartPart:
    def __init__(
        self,
        *,
        name: str,
        text_value: str | None = None,
        chunks: list[bytes] | None = None,
    ) -> None:
        self.name = name
        self._text_value = text_value or ""
        self._chunks = list(chunks or [])

    async def text(self) -> str:
        return self._text_value

    async def read_chunk(self) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    async def release(self) -> None:
        return None


class _FakeMultipartReader:
    def __init__(self, parts: list[_FakeMultipartPart]) -> None:
        self._parts = list(parts)

    async def next(self) -> _FakeMultipartPart | None:
        if not self._parts:
            return None
        return self._parts.pop(0)


class _FakeMultipartRequest:
    def __init__(self, phone: str, parts: list[_FakeMultipartPart]) -> None:
        self.match_info = {"phone": phone}
        self._parts = parts

    async def multipart(self) -> _FakeMultipartReader:
        return _FakeMultipartReader(list(self._parts))


class _FakeJSONRequest:
    def __init__(self, phone: str, body: dict[str, object]) -> None:
        self.match_info = {"phone": phone}
        self._body = body

    async def json(self) -> dict[str, object]:
        return dict(self._body)


class _FakeGoogleProvider:
    last_audio_bytes: bytes = b""

    def __init__(self, _config) -> None:
        pass

    async def transcribe(self, audio_bytes: bytes) -> str:
        type(self).last_audio_bytes = audio_bytes
        return "客户刚完成线下面谈，计划下周再跟进。"


def _server_config(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        channels=SimpleNamespace(
            whatsapp=SimpleNamespace(reply_targets_file=str(tmp_path / "reply_targets.json"))
        )
    )


@pytest.mark.asyncio
async def test_offline_meeting_note_transcription_returns_draft_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )
    server._append_journal = AsyncMock()

    monkeypatch.setattr("nanobot.api.server.load_google_config", lambda: SimpleNamespace())
    monkeypatch.setattr("nanobot.api.server.GoogleSpeechProvider", _FakeGoogleProvider)

    request = _FakeMultipartRequest(
        "1234567890",
        [
            _FakeMultipartPart(name="durationMs", text_value="42000"),
            _FakeMultipartPart(name="audio", chunks=[b"hello", b"world"]),
        ],
    )

    response = await server._handle_transcribe_offline_meeting_note(request)

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["transcript"] == "客户刚完成线下面谈，计划下周再跟进。"
    assert payload["noteId"].startswith("offline_note_")
    assert payload["noteName"] == "笔记1"
    assert _FakeGoogleProvider.last_audio_bytes == b"helloworld"

    bundle_dir = tmp_path / "sessions" / "whatsapp__1234567890"
    assert not bundle_dir.exists()
    assert not (tmp_path / "media").exists()
    assert not (tmp_path / "state").exists()
    server._append_journal.assert_not_awaited()


@pytest.mark.asyncio
async def test_offline_meeting_note_save_appends_note_row_and_list_reads_saved_notes(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )
    server._append_journal = AsyncMock()

    draft_note_id = "offline_note_45B9B5"
    draft_note_name = "笔记1"
    response = await server._handle_save_offline_meeting_note(
        _FakeJSONRequest(
            "1234567890",
            {
                "noteId": draft_note_id,
                "noteName": draft_note_name,
                "transcript": "  第一次会面已确认  ",
            },
        )
    )

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["note"]["transcript"] == "第一次会面已确认"
    assert payload["note"]["noteId"] == draft_note_id
    assert payload["note"]["noteName"] == draft_note_name
    assert payload["note"]["createdAt"]
    server._append_journal.assert_not_awaited()

    bundle_dir = tmp_path / "sessions" / "whatsapp__1234567890"
    session_file = bundle_dir / "session.jsonl"
    meta_file = bundle_dir / "meta.json"
    assert session_file.exists()
    assert meta_file.exists()

    rows = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["_type"] == "metadata"
    assert LEGACY_OFFLINE_MEETING_TRANSCRIPTS_KEY not in rows[0]["metadata"]
    assert rows[1]["_type"] == OFFLINE_MEETING_NOTE_TYPE
    assert rows[1]["note_id"] == draft_note_id
    assert rows[1]["note_name"] == draft_note_name
    assert rows[1]["transcript"] == "第一次会面已确认"
    assert rows[1]["session_key"] == "whatsapp:1234567890"
    assert rows[1]["client_phone"] == "1234567890"

    meta_payload = json.loads(meta_file.read_text(encoding="utf-8"))
    assert LEGACY_OFFLINE_MEETING_TRANSCRIPTS_KEY not in meta_payload["metadata"]
    assert meta_payload[OFFLINE_MEETING_NOTE_INDEX_KEY] == [
        {
            "note_id": payload["note"]["noteId"],
            "note_name": payload["note"]["noteName"],
            "created_at": payload["note"]["createdAt"],
        }
    ]
    assert "第一次会面已确认" not in meta_file.read_text(encoding="utf-8")

    list_response = await server._handle_get_offline_meeting_notes(
        SimpleNamespace(match_info={"phone": "1234567890"})
    )
    assert list_response.status == 200
    assert json.loads(list_response.text) == {
        "notes": [
            {
                "noteId": payload["note"]["noteId"],
                "noteName": payload["note"]["noteName"],
                "createdAt": payload["note"]["createdAt"],
            }
        ],
    }

    detail_response = await server._handle_get_offline_meeting_note_detail(
        SimpleNamespace(
            match_info={
                "phone": "1234567890",
                "noteId": payload["note"]["noteId"],
            }
        )
    )
    assert detail_response.status == 200
    assert json.loads(detail_response.text) == {"note": payload["note"]}


@pytest.mark.asyncio
async def test_offline_meeting_note_save_appends_multiple_rows_newest_last(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    first = await server._handle_save_offline_meeting_note(
        _FakeJSONRequest(
            "1234567890",
            {
                "noteId": "offline_note_111111",
                "noteName": "笔记编号 111111",
                "transcript": "第一次会面",
            },
        )
    )
    second = await server._handle_save_offline_meeting_note(
        _FakeJSONRequest(
            "1234567890",
            {
                "noteId": "offline_note_222222",
                "noteName": "第二次会面重点",
                "transcript": "第二次会面",
            },
        )
    )

    first_note = json.loads(first.text)["note"]
    second_note = json.loads(second.text)["note"]
    session_file = tmp_path / "sessions" / "whatsapp__1234567890" / "session.jsonl"
    rows = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    note_rows = [row for row in rows if row.get("_type") == OFFLINE_MEETING_NOTE_TYPE]

    assert [row["transcript"] for row in note_rows] == ["第一次会面", "第二次会面"]
    assert [row["note_name"] for row in note_rows] == ["笔记编号 111111", "第二次会面重点"]
    assert note_rows[0]["note_id"] == first_note["noteId"]
    assert note_rows[1]["note_id"] == second_note["noteId"]


@pytest.mark.asyncio
async def test_offline_meeting_note_transcription_uses_next_sequential_name_for_matching_saved_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    session_manager.append_offline_meeting_note(
        "whatsapp:1234567890",
        "第一次会面",
        note_name="笔记1",
    )
    session_manager.append_offline_meeting_note(
        "whatsapp:1234567890",
        "自定义标题",
        note_name="会议重点",
    )
    session_manager.append_offline_meeting_note(
        "whatsapp:1234567890",
        "旧编号标题",
        note_name="笔记编号 45B9B5",
    )
    session_manager.append_offline_meeting_note(
        "whatsapp:1234567890",
        "更晚的顺序标题",
        note_name="笔记7",
    )

    monkeypatch.setattr("nanobot.api.server.load_google_config", lambda: SimpleNamespace())
    monkeypatch.setattr("nanobot.api.server.GoogleSpeechProvider", _FakeGoogleProvider)

    response = await server._handle_transcribe_offline_meeting_note(
        _FakeMultipartRequest(
            "1234567890",
            [
                _FakeMultipartPart(name="durationMs", text_value="42000"),
                _FakeMultipartPart(name="audio", chunks=[b"hello", b"world"]),
            ],
        )
    )

    assert response.status == 200
    assert json.loads(response.text)["noteName"] == "笔记8"


@pytest.mark.asyncio
async def test_offline_meeting_note_save_blank_name_uses_next_sequential_default(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    await server._handle_save_offline_meeting_note(
        _FakeJSONRequest(
            "1234567890",
            {
                "noteName": "笔记1",
                "transcript": "第一次会面",
            },
        )
    )

    response = await server._handle_save_offline_meeting_note(
        _FakeJSONRequest(
            "1234567890",
            {
                "noteId": "offline_note_blankname",
                "noteName": "   ",
                "transcript": "第二次会面",
            },
        )
    )

    assert response.status == 200
    payload = json.loads(response.text)
    assert payload["note"]["noteId"] == "offline_note_blankname"
    assert payload["note"]["noteName"] == "笔记2"


@pytest.mark.asyncio
async def test_offline_meeting_note_rejects_over_60_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    load_google_config = AsyncMock()
    monkeypatch.setattr("nanobot.api.server.load_google_config", load_google_config)

    request = _FakeMultipartRequest(
        "1234567890",
        [
            _FakeMultipartPart(name="durationMs", text_value="60001"),
            _FakeMultipartPart(name="audio", chunks=[b"voice"]),
        ],
    )

    response = await server._handle_transcribe_offline_meeting_note(request)

    assert response.status == 400
    assert json.loads(response.text)["error"] == "Recording exceeds the 60-second limit"
    load_google_config.assert_not_called()
    assert not (tmp_path / "sessions" / "whatsapp__1234567890" / "session.jsonl").exists()


@pytest.mark.asyncio
async def test_message_save_preserves_note_rows_and_visible_history_ignores_them(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    note = session_manager.append_offline_meeting_note("whatsapp:1234567890", "客户提过预算较保守")
    session = session_manager.get_or_create("whatsapp:1234567890")
    session.add_message(role="client", content="你好，我想了解医疗保障")
    server._save_whatsapp_session(session)

    persisted = session_manager.read_persisted("whatsapp:1234567890")
    assert [item["transcript"] for item in persisted.offline_meeting_notes] == [note["transcript"]]
    assert [item["content"] for item in persisted.messages] == ["你好，我想了解医疗保障"]

    visible_messages = server._get_session_messages("1234567890")
    assert [item["content"] for item in visible_messages] == ["你好，我想了解医疗保障"]

    rendered = server._render_messages_view_html("1234567890", visible_messages)
    assert "客户提过预算较保守" not in rendered
    assert "你好，我想了解医疗保障" in rendered


def test_offline_meeting_notes_migrate_from_legacy_metadata_on_read(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "sessions" / "whatsapp__1234567890"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    session_file = bundle_dir / "session.jsonl"
    session_file.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "_type": "metadata",
                        "key": "whatsapp:1234567890",
                        "created_at": "2026-04-10T10:00:00+08:00",
                        "updated_at": "2026-04-10T10:00:00+08:00",
                        "metadata": {
                            LEGACY_OFFLINE_MEETING_TRANSCRIPTS_KEY: ["第一次会面", "第二次会面"],
                        },
                        "last_consolidated": 0,
                    },
                    ensure_ascii=False,
                )
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    session_manager = SessionManager(tmp_path)
    session = session_manager.read_persisted("whatsapp:1234567890")

    assert [note["transcript"] for note in session.offline_meeting_notes] == ["第一次会面", "第二次会面"]
    assert LEGACY_OFFLINE_MEETING_TRANSCRIPTS_KEY not in session.metadata

    rows = [json.loads(line) for line in session_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["_type"] == "metadata"
    assert LEGACY_OFFLINE_MEETING_TRANSCRIPTS_KEY not in rows[0]["metadata"]
    assert [row["transcript"] for row in rows[1:]] == ["第一次会面", "第二次会面"]
    assert all(row["_type"] == OFFLINE_MEETING_NOTE_TYPE for row in rows[1:])

    meta_payload = json.loads((bundle_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta_payload[OFFLINE_MEETING_NOTE_INDEX_KEY] == [
        {
            "note_id": rows[1]["note_id"],
            "note_name": rows[1]["note_name"],
            "created_at": rows[1]["created_at"],
        },
        {
            "note_id": rows[2]["note_id"],
            "note_name": rows[2]["note_name"],
            "created_at": rows[2]["created_at"],
        },
    ]


@pytest.mark.asyncio
async def test_offline_meeting_note_detail_is_scoped_to_the_current_client_session(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    first_response = await server._handle_save_offline_meeting_note(
        _FakeJSONRequest("1234567890", {"noteName": "客户A笔记", "transcript": "客户A笔记"})
    )
    await server._handle_save_offline_meeting_note(
        _FakeJSONRequest("9876543210", {"noteName": "客户B笔记", "transcript": "客户B笔记"})
    )
    first_note_id = json.loads(first_response.text)["note"]["noteId"]

    response = await server._handle_get_offline_meeting_note_detail(
        SimpleNamespace(
            match_info={
                "phone": "9876543210",
                "noteId": first_note_id,
            }
        )
    )

    assert response.status == 404
    assert json.loads(response.text)["error"] == "Offline meeting note not found"


@pytest.mark.asyncio
async def test_offline_meeting_note_list_rebuilds_missing_meta_index_from_canonical_rows(tmp_path: Path) -> None:
    session_manager = SessionManager(tmp_path)
    server = ApiServer(
        config=_server_config(tmp_path),
        bus=MessageBus(),
        session_manager=session_manager,
        agent=None,
        channel_manager=SimpleNamespace(enabled_channels=[]),
    )

    save_response = await server._handle_save_offline_meeting_note(
        _FakeJSONRequest("1234567890", {"noteName": "家庭保障补充", "transcript": "补充了家庭保障需求"})
    )
    saved_note = json.loads(save_response.text)["note"]
    meta_file = tmp_path / "sessions" / "whatsapp__1234567890" / "meta.json"
    meta_payload = json.loads(meta_file.read_text(encoding="utf-8"))
    meta_payload.pop(OFFLINE_MEETING_NOTE_INDEX_KEY, None)
    meta_file.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    response = await server._handle_get_offline_meeting_notes(
        SimpleNamespace(match_info={"phone": "1234567890"})
    )

    assert response.status == 200
    assert json.loads(response.text) == {
        "notes": [
            {
                "noteId": saved_note["noteId"],
                "noteName": saved_note["noteName"],
                "createdAt": saved_note["createdAt"],
            }
        ]
    }

    rebuilt_meta_payload = json.loads(meta_file.read_text(encoding="utf-8"))
    assert rebuilt_meta_payload[OFFLINE_MEETING_NOTE_INDEX_KEY] == [
        {
            "note_id": saved_note["noteId"],
            "note_name": saved_note["noteName"],
            "created_at": saved_note["createdAt"],
        }
    ]


def test_agent_loop_exposes_latest_offline_meeting_notes_only() -> None:
    session = Session(
        key="whatsapp:1234567890",
        offline_meeting_notes=[
            {"transcript": "第一次"},
            {"transcript": "第二次"},
            {"transcript": "第三次"},
            {"transcript": "第四次"},
        ],
    )

    metadata = AgentLoop._offline_meeting_runtime_metadata(session)

    assert metadata == {
        "offline_meeting_notes": ["第二次", "第三次", "第四次"]
    }


def test_context_builder_renders_offline_meeting_notes_in_runtime_context() -> None:
    runtime_context = ContextBuilder._build_runtime_context(
        "whatsapp",
        "1234567890@s.whatsapp.net",
        {"offline_meeting_notes": ["客户提过预算较保守", "关心家庭医疗保障"]},
    )

    assert "Offline Meeting Note 1: 客户提过预算较保守" in runtime_context
    assert "Offline Meeting Note 2: 关心家庭医疗保障" in runtime_context


@pytest.mark.asyncio
async def test_launcher_proxy_prefers_nested_offline_meeting_route(tmp_path: Path) -> None:
    launcher = LauncherServer(config=SimpleNamespace(workspace_path=tmp_path))
    launcher._gateway_ready = True
    launcher._api_server = object()

    async def client_handler(_request):
        from aiohttp import web

        return web.json_response({"route": "client"})

    async def notes_handler(_request):
        from aiohttp import web

        return web.json_response({"route": "notes"})

    launcher._get_handler_map = lambda: {
        ("GET", "/api/clients/{phone}"): client_handler,
        ("GET", "/api/clients/{phone}/offline-meeting-notes"): notes_handler,
    }

    response = await launcher._proxy(
        SimpleNamespace(
            path="/api/clients/1234567890/offline-meeting-notes",
            method="GET",
        )
    )

    assert response.status == 200
    assert json.loads(response.text) == {"route": "notes"}


@pytest.mark.asyncio
async def test_launcher_proxy_routes_note_save_endpoint(tmp_path: Path) -> None:
    launcher = LauncherServer(config=SimpleNamespace(workspace_path=tmp_path))
    launcher._gateway_ready = True
    launcher._api_server = object()

    async def save_handler(_request):
        from aiohttp import web

        return web.json_response({"route": "save"})

    launcher._get_handler_map = lambda: {
        ("POST", "/api/clients/{phone}/offline-meeting-note/save"): save_handler,
    }

    response = await launcher._proxy(
        SimpleNamespace(
            path="/api/clients/1234567890/offline-meeting-note/save",
            method="POST",
        )
    )

    assert response.status == 200
    assert json.loads(response.text) == {"route": "save"}


@pytest.mark.asyncio
async def test_launcher_proxy_routes_note_detail_endpoint(tmp_path: Path) -> None:
    launcher = LauncherServer(config=SimpleNamespace(workspace_path=tmp_path))
    launcher._gateway_ready = True
    launcher._api_server = object()

    async def detail_handler(_request):
        from aiohttp import web

        return web.json_response({"route": "detail"})

    launcher._get_handler_map = lambda: {
        ("GET", "/api/clients/{phone}/offline-meeting-notes/{noteId}"): detail_handler,
    }

    response = await launcher._proxy(
        SimpleNamespace(
            path="/api/clients/1234567890/offline-meeting-notes/offline_note_abc123",
            method="GET",
        )
    )

    assert response.status == 200
    assert json.loads(response.text) == {"route": "detail"}
