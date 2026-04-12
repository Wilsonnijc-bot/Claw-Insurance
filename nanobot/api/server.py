"""REST + WebSocket API server for the Nanobot Insurance frontend.

Provides:
- REST endpoints for clients, messages, AI generation, auto-reply, sync
- WebSocket endpoint for real-time updates (new messages, AI progress, status)

Designed to run inside the existing gateway asyncio event loop.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType
from loguru import logger

from nanobot.api.journal import ActivityJournal
from nanobot.config.google_loader import GoogleConfigError, load_google_config
from nanobot.providers.google_speech import GoogleSpeechProvider
from nanobot.session.manager import generate_offline_meeting_note_id

CDP_DRAFT_DISABLED_DETAIL = (
    "WhatsApp Web draft placement is disabled in CDP mode; CDP is reserved for history parsing."
)
OFFLINE_MEETING_MAX_DURATION_MS = 60_000
OFFLINE_MEETING_MAX_AUDIO_BYTES = 12 * 1024 * 1024


class ApiServer:
    """Lightweight aiohttp server that bridges the Nanobot frontend with the backend."""

    def __init__(
        self,
        *,
        config: Any,
        bus: Any,
        session_manager: Any,
        agent: Any,
        channel_manager: Any,
        journal_store: ActivityJournal | None = None,
        bridge_proc: Any = None,
    ):
        self.config = config
        self.bus = bus
        self.session_manager = session_manager
        self.agent = agent
        self.channel_manager = channel_manager
        self.journal_store = journal_store
        self.bridge_proc = bridge_proc  # subprocess.Popen of the Node.js bridge
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._outbound_task: asyncio.Task | None = None
        self._inbound_task: asyncio.Task | None = None
        self._persisted_history_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._bridge_monitor_task: asyncio.Task | None = None
        self._last_auth_status: dict[str, Any] | None = None

        self.app = web.Application(middlewares=[self._cors_middleware])
        self._setup_routes()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/clients", self._handle_get_clients)
        self.app.router.add_get("/api/clients/{phone}", self._handle_get_client)
        self.app.router.add_get(
            "/api/clients/{phone}/offline-meeting-notes",
            self._handle_get_offline_meeting_notes,
        )
        self.app.router.add_get(
            "/api/clients/{phone}/offline-meeting-notes/{noteId}",
            self._handle_get_offline_meeting_note_detail,
        )
        self.app.router.add_post(
            "/api/clients/{phone}/offline-meeting-note/save",
            self._handle_save_offline_meeting_note,
        )
        self.app.router.add_post(
            "/api/clients/{phone}/offline-meeting-note/transcribe",
            self._handle_transcribe_offline_meeting_note,
        )
        self.app.router.add_delete("/api/clients/{phone}", self._handle_delete_client)
        self.app.router.add_get("/api/messages/{phone}", self._handle_get_messages)
        self.app.router.add_post("/api/messages/{phone}", self._handle_send_message)
        self.app.router.add_post("/api/ai-draft/{phone}", self._handle_ai_draft)
        self.app.router.add_post("/api/ai-send/{phone}", self._handle_ai_send)
        self.app.router.add_put("/api/auto-reply/{phone}", self._handle_toggle_auto_reply)
        self.app.router.add_put("/api/auto-draft/{phone}", self._handle_toggle_auto_draft)
        self.app.router.add_post("/api/broadcast", self._handle_broadcast)
        self.app.router.add_post("/api/sync/{phone}", self._handle_sync)
        self.app.router.add_get("/api/journal", self._handle_get_journal)
        self.app.router.add_post("/api/journal", self._handle_add_journal_entry)
        self.app.router.add_delete("/api/journal", self._handle_clear_journal)
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_post("/api/login", self._handle_login)
        self.app.router.add_post("/api/reply-targets", self._handle_add_reply_target)
        self.app.router.add_post("/api/bridge/restart", self._handle_bridge_restart)
        self.app.router.add_get("/ws", self._handle_ws)
        # CORS preflight
        self.app.router.add_route("OPTIONS", "/{path:.*}", self._handle_options)

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        if request.method == "OPTIONS":
            return self._cors_response()
        try:
            resp = await handler(request)
        except web.HTTPException as exc:
            resp = exc
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        return resp

    @staticmethod
    def _cors_response() -> web.Response:
        return web.Response(
            status=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
                "Access-Control-Max-Age": "3600",
            },
        )

    def _handle_options(self, _request: web.Request) -> web.Response:
        return self._cors_response()

    async def _handle_login(self, request: web.Request) -> web.Response:
        """POST /api/login — stub that confirms the gateway is already running."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        username = body.get("username", "agent")
        return web.json_response({
            "status": "ok",
            "message": "Gateway already running",
            "gateway_ready": True,
            "username": username,
        })

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, host: str = "0.0.0.0", port: int = 3456) -> None:
        """Start the HTTP/WS server."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host, port)
        await self._site.start()
        # Start the outbound listener that mirrors bus events to WebSocket
        self._outbound_task = asyncio.create_task(self._mirror_outbound())
        # Start the inbound listener for auto-draft generation
        self._inbound_task = asyncio.create_task(self._mirror_inbound())
        self._persisted_history_task = asyncio.create_task(self._mirror_persisted_history())
        self._status_task = asyncio.create_task(self._monitor_whatsapp_auth_status())
        self._bridge_monitor_task = asyncio.create_task(self._monitor_bridge_process())
        logger.info("API server running on http://{}:{}", host, port)

    async def stop(self) -> None:
        """Stop the server gracefully."""
        if self._outbound_task:
            self._outbound_task.cancel()
            try:
                await self._outbound_task
            except asyncio.CancelledError:
                pass
        if self._inbound_task:
            self._inbound_task.cancel()
            try:
                await self._inbound_task
            except asyncio.CancelledError:
                pass
        if self._persisted_history_task:
            self._persisted_history_task.cancel()
            try:
                await self._persisted_history_task
            except asyncio.CancelledError:
                pass
        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass
        if self._bridge_monitor_task:
            self._bridge_monitor_task.cancel()
            try:
                await self._bridge_monitor_task
            except asyncio.CancelledError:
                pass
        for ws in list(self._ws_clients):
            await ws.close()
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("API server stopped")

    # ------------------------------------------------------------------
    # WebSocket real-time
    # ------------------------------------------------------------------

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)
        logger.info("WebSocket client connected (total: {})", len(self._ws_clients))

        bridge_status = self._get_whatsapp_bridge_status()
        await ws.send_json({
            "type": "whatsapp_bridge_status",
            "bridgeError": bridge_status.get("error"),
            "message": bridge_status.get("message"),
        })
        auth_status = self._get_whatsapp_auth_status()
        await ws.send_json({
            "type": "whatsapp_auth_status",
            "required": auth_status.get("required"),
            "qr": auth_status.get("qr"),
            "message": auth_status.get("message"),
        })

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Client can send pings or commands
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "ping":
                            await ws.send_json({"type": "pong"})
                    except Exception:
                        pass
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._ws_clients.discard(ws)
            logger.info("WebSocket client disconnected (total: {})", len(self._ws_clients))

        return ws

    async def _broadcast_ws(self, event: dict) -> None:
        """Send an event to all connected WebSocket clients."""
        if not self._ws_clients:
            return
        payload = json.dumps(event, ensure_ascii=False, default=str)
        closed = []
        for ws in self._ws_clients:
            try:
                await ws.send_str(payload)
            except Exception:
                closed.append(ws)
        for ws in closed:
            self._ws_clients.discard(ws)

    def _get_whatsapp_bridge_status(self) -> dict[str, Any]:
        """Return the latest WhatsApp bridge-process status."""
        whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
        if whatsapp and hasattr(whatsapp, "get_bridge_status"):
            try:
                status = whatsapp.get_bridge_status()
                if isinstance(status, dict):
                    return status
            except Exception:
                logger.exception("Failed to read WhatsApp bridge status")
        return {"error": False, "message": ""}

    @staticmethod
    def _env_flag(name: str, default: bool = True) -> bool:
        raw = str(os.environ.get(name, str(default))).strip().lower()
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _whatsapp_helper_platform() -> str:
        raw = str(os.environ.get("WEB_CDP_HELPER_PLATFORM") or "").strip().lower()
        if raw:
            return raw
        helper_url = str(os.environ.get("WEB_CDP_HELPER_URL") or "").strip()
        return "macos" if helper_url else ""

    @staticmethod
    def _whatsapp_helper_install_command(platform_name: str) -> str:
        if platform_name == "macos":
            return "python3 -m nanobot.macos_cdp_helper install"
        if platform_name == "linux":
            return "python -m nanobot install-linux-cdp-helper"
        if platform_name == "windows":
            return "python -m nanobot install-windows-cdp-helper"
        return "python -m nanobot install-host-cdp-helper"

    @staticmethod
    def _whatsapp_docker_up_hint(platform_name: str) -> str:
        if platform_name == "macos":
            return "python -m nanobot docker-up（macOS 也可继续使用 ./docker-up）"
        return "python -m nanobot docker-up"

    @staticmethod
    def _whatsapp_helper_probe(platform_name: str):
        if platform_name == "macos":
            from nanobot.macos_cdp_helper import request_helper_health

            return request_helper_health
        if platform_name == "linux":
            from nanobot.linux_cdp_helper import request_helper_health

            return request_helper_health
        if platform_name == "windows":
            from nanobot.windows_cdp_helper import request_helper_health

            return request_helper_health
        raise RuntimeError(f"Unsupported host helper platform: {platform_name or 'unknown'}")

    def _get_whatsapp_sync_status(self) -> dict[str, Any]:
        """Return whether WhatsApp history sync is prepared for this runtime."""
        if not self._env_flag("WEB_HISTORY_SYNC_ENABLED", default=True):
            platform_name = self._whatsapp_helper_platform()
            return {
                "available": False,
                "message": (
                    "当前 Docker 主机未准备 WhatsApp 历史同步。应用仍可正常使用；"
                    f"如需历史同步，请在宿主机运行 {self._whatsapp_docker_up_hint(platform_name)} 完成预检。"
                ),
            }

        browser_mode = str(os.environ.get("WEB_BROWSER_MODE") or "").strip().lower()
        helper_url = str(os.environ.get("WEB_CDP_HELPER_URL") or "").strip()
        platform_name = self._whatsapp_helper_platform()
        running_in_docker_workspace = str(os.environ.get("NANOBOT_PROJECT_ROOT") or "").strip() == "/workspace"

        if browser_mode == "cdp" and running_in_docker_workspace:
            if not helper_url:
                return {
                    "available": False,
                    "message": (
                        "Docker WhatsApp 历史同步尚未配置主机侧 CDP helper。"
                        f"请使用 {self._whatsapp_docker_up_hint(platform_name)} 启动，或手动提供 WEB_CDP_HELPER_URL。"
                    ),
                }

            try:
                request_helper_health = self._whatsapp_helper_probe(platform_name)

                if not request_helper_health(helper_url, timeout_s=0.4):
                    return {
                        "available": False,
                        "message": (
                            f"主机侧 CDP helper 未就绪：{helper_url}。"
                            f"请在宿主机运行 {self._whatsapp_docker_up_hint(platform_name)}，或执行 "
                            f"{self._whatsapp_helper_install_command(platform_name)} 后重试。"
                        ),
                    }
                return {"available": True, "message": ""}
            except Exception:
                logger.exception("Failed to probe WhatsApp sync helper health")
                return {
                    "available": False,
                    "message": (
                        f"无法检查主机侧 CDP helper：{helper_url}。"
                        f"请在宿主机重新运行 {self._whatsapp_docker_up_hint(platform_name)} 后重试。"
                    ),
                }

        return {"available": True, "message": ""}

    async def _broadcast_current_whatsapp_bridge_status(self) -> None:
        """Push the latest WhatsApp bridge status to connected UI clients."""
        bridge_status = self._get_whatsapp_bridge_status()
        await self._broadcast_ws(
            {
                "type": "whatsapp_bridge_status",
                "bridgeError": bridge_status.get("error"),
                "message": bridge_status.get("message"),
            }
        )

    def _get_whatsapp_auth_status(self) -> dict[str, Any]:
        """Return the latest WhatsApp Baileys auth status."""
        whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
        if whatsapp and hasattr(whatsapp, "get_auth_status"):
            try:
                status = whatsapp.get_auth_status()
                if isinstance(status, dict):
                    return status
            except Exception:
                logger.exception("Failed to read WhatsApp auth status")
        return {"required": False, "qr": "", "message": ""}

    async def _monitor_whatsapp_auth_status(self) -> None:
        """Broadcast WhatsApp auth-state changes to UI clients."""
        try:
            while True:
                auth_status = self._get_whatsapp_auth_status()
                auth_comparable = {
                    "required": auth_status.get("required"),
                    "qr": auth_status.get("qr"),
                    "message": auth_status.get("message"),
                }
                if auth_comparable != self._last_auth_status:
                    self._last_auth_status = auth_comparable
                    await self._broadcast_ws({
                        "type": "whatsapp_auth_status",
                        **auth_comparable,
                    })
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    async def _monitor_bridge_process(self) -> None:
        """Detect bridge process crashes and notify the frontend.

        Polls ``self.bridge_proc`` every 5 s.  When the process exits
        unexpectedly, the cached browser status is set to ``error`` severity
        and a WS broadcast goes out immediately so the frontend can show a
        reconnect button.
        """
        try:
            while True:
                await asyncio.sleep(5.0)
                proc = self.bridge_proc
                if proc is None:
                    continue
                exit_code = proc.poll()
                if exit_code is not None:
                    logger.error("Bridge process exited with code {} — notifying frontend", exit_code)
                    whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
                    if whatsapp and hasattr(whatsapp, "_set_bridge_status"):
                        whatsapp._set_bridge_status(
                            True,
                            f"Bridge 进程已崩溃 (exit {exit_code})。请点击重启按钮恢复连接。",
                        )
                    await self._broadcast_ws({
                        "type": "whatsapp_bridge_status",
                        "bridgeError": True,
                        "message": f"Bridge 进程已崩溃 (exit {exit_code})。请点击重启按钮恢复连接。",
                    })
                    # Clear the reference so we stop polling a dead process
                    self.bridge_proc = None
        except asyncio.CancelledError:
            return

    def _restart_bridge_sync(self) -> dict[str, str]:
        """Restart the WhatsApp bridge process (kill → rebuild → start).

        This is a **blocking** method meant to be called via
        ``run_in_executor``.  Returns a dict with ``status`` and ``message``
        for the API response.
        """
        import subprocess
        from nanobot.cli.commands import (
            _stop_whatsapp_bridge,
            _get_bridge_dir,
            _build_whatsapp_bridge_env,
            _whatsapp_bridge_running,
        )

        # 1. Kill existing bridge
        if self.bridge_proc and self.bridge_proc.poll() is None:
            try:
                _stop_whatsapp_bridge(self.bridge_proc)
            except Exception:
                logger.warning("Failed to stop old bridge — it may have already exited")
            self.bridge_proc = None

        # 2. Rebuild bridge from source (invalidates the cache)
        import shutil
        build_dir = Path(__file__).resolve().parents[2] / ".bridge-build"
        if build_dir.exists():
            shutil.rmtree(build_dir)
            logger.info("Deleted stale .bridge-build/ cache")

        bridge_dir = _get_bridge_dir()

        # 3. Restart bridge
        env = _build_whatsapp_bridge_env(self.config)
        proc = subprocess.Popen(
            ["npm", "start"],
            cwd=bridge_dir,
            env=env,
            start_new_session=True,
        )

        # 4. Wait for bridge to become reachable
        import time
        deadline = time.time() + 15
        while time.time() < deadline:
            if proc.poll() is not None:
                return {"status": "error", "message": f"Bridge exited early with code {proc.returncode}"}
            if _whatsapp_bridge_running(self.config):
                self.bridge_proc = proc
                # Also update the launcher's reference if accessible
                logger.info("Bridge restarted successfully (PID {})", proc.pid)
                return {"status": "ok", "message": f"Bridge restarted (PID {proc.pid})"}
            time.sleep(0.5)

        proc.terminate()
        return {"status": "error", "message": "Bridge did not become ready within 15 s"}

    async def _mirror_outbound(self) -> None:
        """Mirror outbound bus messages to WebSocket clients.

        This runs alongside the ChannelManager's own outbound dispatcher.
        We use a secondary observer approach: we tap into the bus by
        subscribing to outbound events after ChannelManager consumes them.
        """
        # We register an observer on the bus instead of consuming
        # (consuming would steal messages from ChannelManager).
        # This is done via the _outbound_observers list we add to the bus.
        if not hasattr(self.bus, '_outbound_observers'):
            self.bus._outbound_observers = []
        observer_queue: asyncio.Queue = asyncio.Queue()
        self.bus._outbound_observers.append(observer_queue)

        try:
            while True:
                msg = await observer_queue.get()
                metadata = msg.metadata or {}
                is_progress = metadata.get("_progress", False)
                # Skip internal commands (sync, replay, etc.)
                if metadata.get("_internal_command"):
                    continue
                # Extract phone from chat_id (e.g. "85268424658@s.whatsapp.net" -> "85268424658")
                phone = msg.chat_id.split("@")[0] if "@" in (msg.chat_id or "") else msg.chat_id
                event = {
                    "type": "ai_progress" if is_progress else "new_message",
                    "channel": msg.channel,
                    "phone": phone,
                    "chat_id": msg.chat_id,
                    "content": msg.content,
                    "sender": "ai" if is_progress else "agent",
                    "timestamp": datetime.now().isoformat(),
                    "metadata": {
                        k: v for k, v in metadata.items()
                        if not k.startswith("_")
                    },
                }
                await self._broadcast_ws(event)
        except asyncio.CancelledError:
            self.bus._outbound_observers.remove(observer_queue)

    async def _mirror_persisted_history(self) -> None:
        """Mirror persisted JSONL history updates to the existing frontend event shape."""
        observer_queue = self.bus.add_persisted_history_observer()

        try:
            while True:
                event = await observer_queue.get()
                await self._broadcast_ws({
                    "type": "new_message",
                    "channel": event.channel,
                    "phone": event.phone,
                    "chat_id": event.chat_id,
                    "content": event.content,
                    "sender": event.sender,
                    "timestamp": event.timestamp or datetime.now().isoformat(),
                    "metadata": {
                        "changeType": event.change_type,
                        **{
                            k: v for k, v in (event.metadata or {}).items()
                            if not str(k).startswith("_")
                        },
                    },
                })
        except asyncio.CancelledError:
            self.bus.remove_persisted_history_observer(observer_queue)

    async def _append_journal(
        self,
        *,
        action: str,
        description: str,
        client_id: str | None = None,
        client_name: str | None = None,
        details: dict[str, Any] | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        source: str = "backend",
    ) -> dict[str, Any] | None:
        if not self.journal_store:
            return None
        entry = await self.journal_store.log(
            action=action,
            description=description,
            client_id=client_id,
            client_name=client_name,
            details=details,
            user_id=user_id,
            user_name=user_name,
            source=source,
        )
        await self._broadcast_ws({"type": "journal_entry", "entry": entry})
        return entry

    def _journal_client_name(self, phone: str) -> str:
        target_name = ""
        targets = self._load_reply_targets()
        for target in targets.get("direct_reply_targets", []):
            if target.get("phone") == phone:
                target_name = str(target.get("label") or target.get("push_name") or "")
                break

        session_key = self._phone_to_session_key(phone)
        meta_path = self.session_manager.get_session_meta_path(session_key)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                return (
                    meta.get("display_name")
                    or meta.get("client_display_name")
                    or meta.get("client_name")
                    or meta.get("client_push_name")
                    or target_name
                    or phone
                )
            except Exception:
                pass
        return target_name or phone

    @staticmethod
    def _preview_text(content: str, limit: int = 80) -> str:
        text = " ".join((content or "").split())
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    async def _mirror_inbound(self) -> None:
        """Observe inbound bus messages for journaling and auto-draft only.

        When the UI is connected, the MessageBus marks WhatsApp inbound
        messages as capture_only + _auto_draft_candidate.  This task:
        1. Logs inbound activity for the operator journal.
        2. For auto_draft_candidate messages from clients with auto_draft
           enabled, spawns an AI draft that arrives directly in the UI
           composer box.
        """
        observer_queue: asyncio.Queue = asyncio.Queue()
        self.bus._inbound_observers.append(observer_queue)

        try:
            while True:
                msg = await observer_queue.get()
                if msg.channel != "whatsapp":
                    continue

                phone = msg.chat_id.split("@")[0] if "@" in (msg.chat_id or "") else msg.chat_id
                content = msg.content or ""
                if not phone or not content:
                    continue

                client_name = self._journal_client_name(phone)
                await self._append_journal(
                    action="INBOUND_MESSAGE",
                    description=f"收到 {client_name} 的新消息",
                    client_id=phone,
                    client_name=client_name,
                    details={"preview": self._preview_text(content)},
                )

                # 2. Check for auto-draft
                if not msg.metadata.get("_auto_draft_candidate"):
                    continue
                if not self._ws_clients:
                    continue

                # Check if this client has auto_draft enabled
                targets = self._load_reply_targets()
                target = next(
                    (t for t in targets.get("direct_reply_targets", [])
                     if t.get("phone") == phone and t.get("auto_draft")),
                    None,
                )
                if not target:
                    continue

                # Spawn auto-draft generation (non-blocking)
                asyncio.create_task(self._auto_generate_draft(phone, content))
        except asyncio.CancelledError:
            self.bus._inbound_observers.remove(observer_queue)

    async def _auto_generate_draft(self, phone: str, client_message: str) -> None:
        """Generate an AI draft and send it to the UI composer via WebSocket."""
        try:
            key = self._phone_to_session_key(phone)
            chat_id = self._resolve_chat_id(phone)

            # Notify UI that AI is generating
            await self._broadcast_ws({
                "type": "ai_generating",
                "phone": phone,
                "status": "started",
            })

            # Wait briefly for the agent loop to finish capturing the message
            await asyncio.sleep(0.3)

            response = await self.agent.process_direct(
                client_message,
                session_key=key,
                channel="whatsapp",
                chat_id=chat_id,
                persist_history=False,
            )

            if response and self._ws_clients:
                await self._broadcast_ws({
                    "type": "auto_draft",
                    "phone": phone,
                    "content": response,
                    "timestamp": datetime.now().isoformat(),
                })
                client_name = self._journal_client_name(phone)
                await self._append_journal(
                    action="AUTO_DRAFT_READY",
                    description=f"已为 {client_name} 生成自动草稿",
                    client_id=phone,
                    client_name=client_name,
                    details={"preview": self._preview_text(response)},
                )
            else:
                await self._broadcast_ws({
                    "type": "ai_generating",
                    "phone": phone,
                    "status": "no_response",
                })
        except Exception:
            logger.exception("Auto-draft generation failed for {}", phone)
            await self._broadcast_ws({
                "type": "ai_generating",
                "phone": phone,
                "status": "error",
            })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _reply_targets_path(self) -> Path:
        """Resolve the reply targets JSON file path."""
        from nanobot.channels.whatsapp_reply_targets import reply_targets_path

        wa_cfg = self.config.channels.whatsapp
        return reply_targets_path(wa_cfg.reply_targets_file, Path(__file__).resolve().parents[2])

    def _load_reply_targets(self) -> dict:
        from nanobot.channels.whatsapp_reply_targets import load_reply_targets

        wa_cfg = self.config.channels.whatsapp
        return load_reply_targets(
            self._reply_targets_path(),
            project_root=Path(__file__).resolve().parents[2],
            group_members_file=str(getattr(wa_cfg, "group_members_file", "") or ""),
        )

    def _save_reply_targets(self, data: dict) -> None:
        p = self._reply_targets_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = datetime.now().isoformat()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _remove_reply_target(self, phone: str) -> bool:
        """Remove a direct reply target by normalized phone."""
        from nanobot.channels.whatsapp_contacts import normalize_contact_id

        target_phone = normalize_contact_id(phone)
        data = self._load_reply_targets()
        direct_targets = data.get("direct_reply_targets", [])
        kept_targets = [
            target
            for target in direct_targets
            if normalize_contact_id(str(target.get("phone") or "")) != target_phone
        ]
        removed = len(kept_targets) != len(direct_targets)
        if removed:
            data["direct_reply_targets"] = kept_targets
            self._save_reply_targets(data)
        return removed

    def _resolve_client_key(self, phone: str) -> "ClientKey":
        """Derive a validated ClientKey from a raw phone string."""
        from nanobot.session.client_key import ClientKey
        return ClientKey.normalize(phone)

    def _phone_to_session_key(self, phone: str) -> str:
        return self._resolve_client_key(phone).session_key

    def _draft_delivery_disabled_response(self) -> web.Response | None:
        """Block UI send flows when draft delivery would require parse-only CDP."""
        channels = getattr(self.config, "channels", None)
        whatsapp_cfg = getattr(channels, "whatsapp", None)
        if whatsapp_cfg is None:
            return None

        if (
            str(getattr(whatsapp_cfg, "delivery_mode", "") or "") == "draft"
            and str(getattr(whatsapp_cfg, "web_browser_mode", "") or "") == "cdp"
        ):
            return web.json_response(
                {
                    "error": CDP_DRAFT_DISABLED_DETAIL,
                    "code": "draft_delivery_disabled",
                },
                status=409,
            )
        return None

    def _save_whatsapp_session(
        self,
        session: Any,
        *,
        change_type: str = "updated",
        metadata: dict[str, Any] | None = None,
        notify_observers: bool = False,
    ) -> None:
        """Persist a WhatsApp session through the canonical JSONL write path."""
        self.session_manager.save_history(
            session,
            bus=self.bus,
            change_type=change_type,
            metadata=metadata,
            notify_observers=notify_observers,
        )

    @staticmethod
    def _serialize_offline_meeting_note(note: dict[str, Any]) -> dict[str, str]:
        """Return the frontend response shape for one saved offline-meeting note."""
        return {
            "noteId": str(note.get("note_id") or ""),
            "noteName": str(note.get("note_name") or ""),
            "transcript": str(note.get("transcript") or ""),
            "createdAt": str(note.get("created_at") or ""),
        }

    @staticmethod
    def _serialize_offline_meeting_note_index_entry(entry: dict[str, Any]) -> dict[str, str]:
        """Return the frontend response shape for one lightweight note index item."""
        return {
            "noteId": str(entry.get("note_id") or ""),
            "noteName": str(entry.get("note_name") or ""),
            "createdAt": str(entry.get("created_at") or ""),
        }

    @staticmethod
    def _sanitize_saved_offline_meeting_transcript(raw_value: Any) -> str:
        """Trim the final saved transcript while preserving user edits inside the text."""
        if not isinstance(raw_value, str):
            raise ValueError("transcript must be a string")
        transcript = raw_value.strip()
        if not transcript:
            raise ValueError("transcript must not be empty")
        return transcript

    @staticmethod
    def _sanitize_saved_offline_meeting_note_id(raw_value: Any) -> str | None:
        """Validate an optional draft note id carried from transcription to save."""
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise ValueError("noteId must be a string")
        note_id = raw_value.strip()
        if not note_id:
            return None
        if not note_id.startswith("offline_note_"):
            raise ValueError("noteId must start with offline_note_")
        return note_id

    @staticmethod
    def _sanitize_saved_offline_meeting_note_name(raw_value: Any) -> str | None:
        """Trim the final saved note name while preserving blank as backend-default."""
        if raw_value is None:
            return None
        if not isinstance(raw_value, str):
            raise ValueError("noteName must be a string")
        note_name = raw_value.strip()
        if not note_name:
            return None
        return note_name

    def _get_offline_meeting_notes(self, phone: str) -> list[dict[str, str]]:
        client = self._resolve_client_key(phone)
        index = self.session_manager.read_offline_meeting_note_index(client.session_key)
        return [
            self._serialize_offline_meeting_note_index_entry(entry)
            for entry in index
        ]

    def _get_offline_meeting_note_detail(self, phone: str, note_id: str) -> dict[str, str]:
        client = self._resolve_client_key(phone)
        note = self.session_manager.find_offline_meeting_note(client.session_key, note_id)
        if note is None:
            raise LookupError("Offline meeting note not found")
        return self._serialize_offline_meeting_note(note)

    def _save_offline_meeting_note(
        self,
        phone: str,
        transcript: Any,
        note_name: Any,
        note_id: Any = None,
    ) -> dict[str, str]:
        client = self._resolve_client_key(phone)
        final_note_id = self._sanitize_saved_offline_meeting_note_id(note_id) or generate_offline_meeting_note_id()
        final_note_name = self._sanitize_saved_offline_meeting_note_name(note_name)
        final_transcript = self._sanitize_saved_offline_meeting_transcript(transcript)
        note = self.session_manager.append_offline_meeting_note(
            client.session_key,
            final_transcript,
            note_id=final_note_id,
            note_name=final_note_name,
        )
        return self._serialize_offline_meeting_note(note)

    async def _read_offline_meeting_audio_upload(
        self,
        request: web.Request,
    ) -> tuple[bytes, int]:
        reader = await request.multipart()
        audio_buffer = bytearray()
        duration_ms: int | None = None
        has_audio = False

        while True:
            part = await reader.next()
            if part is None:
                break

            name = str(getattr(part, "name", "") or "")
            if name == "durationMs":
                raw_duration = (await part.text()).strip()
                try:
                    duration_ms = int(raw_duration)
                except (TypeError, ValueError) as exc:
                    raise ValueError("durationMs must be an integer number of milliseconds") from exc
                continue

            if name != "audio":
                await part.release()
                continue

            has_audio = True
            while True:
                chunk = await part.read_chunk()
                if not chunk:
                    break
                audio_buffer.extend(chunk)
                if len(audio_buffer) > OFFLINE_MEETING_MAX_AUDIO_BYTES:
                    raise ValueError("Uploaded audio is too large for a 60-second voice note")

        if not has_audio:
            raise ValueError("Missing audio upload")
        if duration_ms is None:
            raise ValueError("Missing durationMs")
        if duration_ms <= 0:
            raise ValueError("durationMs must be greater than 0")
        if duration_ms > OFFLINE_MEETING_MAX_DURATION_MS:
            raise ValueError("Recording exceeds the 60-second limit")
        if not audio_buffer:
            raise ValueError("Uploaded audio is empty")

        return bytes(audio_buffer), duration_ms

    def _get_session_messages(self, phone: str) -> list[dict]:
        """Return visible messages for a WhatsApp phone session."""
        client = self._resolve_client_key(phone)
        session = self.session_manager.read_persisted_for_client(client)
        messages = []
        for m in session.messages:
            role = m.get("role", "")
            # Skip tool/system messages
            if role in ("tool", "system"):
                continue
            content = m.get("content", "")
            if not content or not content.strip():
                continue
            # Skip deleted
            if m.get("deleted_by_sender"):
                continue
            item = {
                "id": m.get("message_id", m.get("timestamp", "")),
                "role": role,
                "sender": "client" if role == "client" else ("ai" if m.get("is_ai_draft") else "agent"),
                "content": content,
                "timestamp": m.get("timestamp", ""),
                "isAIDraft": bool(m.get("is_ai_draft")),
                "fromMe": role in ("me", "assistant"),
            }
            message_type = str(m.get("message_type", "") or "").strip()
            if message_type:
                item["messageType"] = message_type
            reply_text = str(m.get("reply_text", "") or "").strip()
            if reply_text:
                item["replyText"] = reply_text
            quoted_text = str(m.get("quoted_text", "") or "")
            if quoted_text.strip():
                item["quotedText"] = quoted_text
            quoted_message_id = str(m.get("quoted_message_id", "") or "").strip()
            if quoted_message_id:
                item["quotedMessageId"] = quoted_message_id
            messages.append(item)
        return messages

    def _render_messages_view_html(self, phone: str, messages: list[dict]) -> str:
        """Render a standalone transcript document from session-backed messages."""
        targets = self._load_reply_targets()
        target = next(
            (item for item in targets.get("direct_reply_targets", []) if str(item.get("phone") or "") == phone),
            None,
        )
        client_name = str(
            (target or {}).get("label")
            or (target or {}).get("push_name")
            or (f"+{phone}" if phone else "Client")
        )
        masked_name = f"{client_name[:1]}**" if client_name else "Client"

        def render_timestamp(value: str) -> str:
            formatted = _format_time(value)
            return formatted or ""

        rows: list[str] = []
        for index, message in enumerate(messages):
            sender = str(message.get("sender") or "agent")
            role_class = {
                "client": "client",
                "ai": "ai",
                "agent": "agent",
            }.get(sender, "agent")
            avatar = (
                html.escape(client_name[:1] or "C")
                if role_class == "client"
                else ("AI" if role_class == "ai" else "\u6211")
            )
            content = html.escape(str(message.get("content") or ""))
            message_type = str(message.get("messageType") or "")
            quoted_text = html.escape(str(message.get("quotedText") or ""))
            bubble_content = content
            if message_type == "imported_client_reply_with_quote" and quoted_text:
                bubble_content = (
                    f'<div class="quoted-block">{quoted_text}</div>'
                    f'<div class="reply-block">{content}</div>'
                )
            timestamp = html.escape(render_timestamp(str(message.get("timestamp") or "")))
            rows.append(
                f"""
                <article class="row {role_class}" style="animation-delay: {index * 50}ms">
                  <div class="avatar">{avatar}</div>
                  <div class="bubble-wrap">
                    <div class="bubble">{bubble_content}</div>
                    <div class="timestamp">{timestamp}</div>
                  </div>
                </article>
                """
            )

        empty_state = ""
        if not rows:
            empty_state = f"""
              <section class="empty">
                <div class="empty-icon">AI</div>
                <h2>\u5f00\u59cb\u4e0e {html.escape(masked_name)} \u7684\u5bf9\u8bdd</h2>
                <p>AI\u52a9\u624b\u5c06\u5e2e\u52a9\u60a8\u63d0\u4f9b\u4e13\u4e1a\u7684\u4fdd\u9669\u5efa\u8bae</p>
              </section>
            """

        transcript = "".join(rows)
        safe_title = html.escape(client_name)
        return f"""<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title}</title>
    <style>
      :root {{
        color-scheme: light;
        --bg-start: #ffffff;
        --bg-end: #f8fafc;
        --ink: #0f172a;
        --muted: #64748b;
        --line: #e2e8f0;
        --client: #ffffff;
        --ai: #e0f2fe;
        --agent: #1d4ed8;
      }}
      * {{ box-sizing: border-box; }}
      html, body {{ margin: 0; padding: 0; min-height: 100%; }}
      body {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: linear-gradient(180deg, var(--bg-start), var(--bg-end));
        color: var(--ink);
        padding: 20px;
      }}
      .thread {{
        display: flex;
        flex-direction: column;
        gap: 16px;
        min-height: calc(100vh - 40px);
      }}
      .empty {{
        margin: auto;
        text-align: center;
        max-width: 320px;
        color: var(--muted);
      }}
      .empty-icon {{
        width: 64px;
        height: 64px;
        margin: 0 auto 16px;
        border-radius: 20px;
        display: grid;
        place-items: center;
        font-weight: 700;
        color: #0f172a;
        border: 1px solid rgba(29, 78, 216, 0.1);
        background: linear-gradient(135deg, rgba(29, 78, 216, 0.08), rgba(29, 78, 216, 0.02));
      }}
      .empty h2 {{
        margin: 0 0 6px;
        font-size: 18px;
        color: var(--ink);
      }}
      .empty p {{
        margin: 0;
        font-size: 14px;
        line-height: 1.6;
      }}
      .row {{
        display: flex;
        gap: 10px;
        max-width: 80%;
        animation: fade-in 220ms ease both;
      }}
      .row.client {{
        align-self: flex-start;
      }}
      .row.ai,
      .row.agent {{
        align-self: flex-end;
        flex-direction: row-reverse;
      }}
      .avatar {{
        width: 32px;
        height: 32px;
        border-radius: 999px;
        flex: 0 0 auto;
        display: grid;
        place-items: center;
        font-size: 12px;
        font-weight: 700;
        color: #ffffff;
        background: linear-gradient(135deg, #0f4c81, #12355b);
      }}
      .row.ai .avatar {{
        background: #e0f2fe;
        color: #0f172a;
        border: 1px solid rgba(14, 165, 233, 0.25);
      }}
      .bubble-wrap {{
        display: flex;
        flex-direction: column;
      }}
      .bubble {{
        padding: 10px 14px;
        border-radius: 18px;
        font-size: 14px;
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
      }}
      .quoted-block {{
        margin-bottom: 8px;
        padding: 8px 10px;
        border-radius: 12px;
        border-left: 3px solid rgba(100, 116, 139, 0.45);
        background: rgba(148, 163, 184, 0.12);
        color: var(--muted);
        font-size: 12px;
        line-height: 1.5;
      }}
      .reply-block {{
        white-space: pre-wrap;
      }}
      .row.client .bubble {{
        background: var(--client);
        border: 1px solid var(--line);
        border-top-left-radius: 6px;
        box-shadow: 0 10px 25px rgba(15, 23, 42, 0.05);
      }}
      .row.agent .quoted-block,
      .row.ai .quoted-block {{
        background: rgba(255, 255, 255, 0.18);
        color: rgba(255, 255, 255, 0.88);
        border-left-color: rgba(255, 255, 255, 0.45);
      }}
      .row.ai .bubble {{
        background: var(--ai);
        border: 1px solid rgba(14, 165, 233, 0.18);
        border-top-right-radius: 6px;
      }}
      .row.agent .bubble {{
        background: var(--agent);
        color: #ffffff;
        border-top-right-radius: 6px;
        box-shadow: 0 10px 20px rgba(29, 78, 216, 0.18);
      }}
      .timestamp {{
        margin-top: 4px;
        font-size: 10px;
        color: var(--muted);
      }}
      .row.client .timestamp {{
        margin-left: 4px;
      }}
      .row.ai .timestamp,
      .row.agent .timestamp {{
        margin-right: 4px;
        text-align: right;
      }}
      @keyframes fade-in {{
        from {{
          opacity: 0;
          transform: translateY(8px);
        }}
        to {{
          opacity: 1;
          transform: translateY(0);
        }}
      }}
    </style>
  </head>
  <body>
    <main class="thread">
      {empty_state}
      {transcript}
    </main>
    <script>
      window.addEventListener('load', function () {{
        window.scrollTo({{ top: document.body.scrollHeight, behavior: 'auto' }});
      }});
    </script>
  </body>
</html>
"""

    def _client_summary(self, target: dict, session_messages: list[dict]) -> dict:
        """Build a client summary object for the frontend."""
        phone = target.get("phone", "")
        push_name = target.get("push_name", "")
        label = str(target.get("label", "") or "")
        name = label or push_name or (f"+{phone}" if phone else "Unknown")
        last_msg = ""
        last_time = target.get("last_seen_at", "")
        session_key = self._phone_to_session_key(phone)
        session_path = self.session_manager.get_session_path(session_key)
        readable_dir = self.session_manager.get_readable_session_dir(session_key)
        if session_messages:
            last = session_messages[-1]
            last_msg = last.get("content", "")[:80]
            last_time = last.get("timestamp", last_time)

        # Read session meta for extra info
        meta_path = self.session_manager.get_session_meta_path(session_key)
        meta_info = {}
        if meta_path.exists():
            try:
                meta_info = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass

        return {
            "id": phone,
            "phone": phone,
            "name": name,
            "status": "offline",  # We don't track online status directly
            "lastMessage": last_msg,
            "lastMessageTime": _format_time(last_time),
            "autoReplyEnabled": target.get("enabled", False),
            "autoDraftEnabled": target.get("auto_draft", False),
            "tags": _infer_tags(target, session_messages),
            "chatId": target.get("chat_id", ""),
            "senderId": target.get("sender_id", ""),
            "pushName": push_name,
            "label": label,
            "sessionFile": str(session_path),
            "sessionReadableDir": str(readable_dir),
            "sessionMetaFile": str(self.session_manager.get_session_meta_path(session_key)),
            "sessionHistoryFile": str(session_path),
            "sessionReadableFile": str(session_path),
            "messageCount": meta_info.get("message_count", len(session_messages)),
            "createdAt": meta_info.get("created_at", ""),
            "updatedAt": meta_info.get("updated_at", ""),
            "clientDisplayName": meta_info.get("display_name", "") or meta_info.get("client_name", "") or name,
            "clientPhone": meta_info.get("client_phone", "") or phone,
            "clientChatId": meta_info.get("client_chat_id", "") or target.get("chat_id", ""),
        }

    # ------------------------------------------------------------------
    # Route Handlers
    # ------------------------------------------------------------------

    async def _handle_get_clients(self, _request: web.Request) -> web.Response:
        """GET /api/clients — list all WhatsApp reply targets as client objects."""
        try:
            targets = self._load_reply_targets()
            clients = []
            for t in targets.get("direct_reply_targets", []):
                msgs = self._get_session_messages(t.get("phone", ""))
                clients.append(self._client_summary(t, msgs))
            return web.json_response({"clients": clients})
        except Exception:
            logger.exception("Error listing clients")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_get_client(self, request: web.Request) -> web.Response:
        """GET /api/clients/:phone — single client detail."""
        phone = request.match_info["phone"]
        try:
            targets = self._load_reply_targets()
            target = None
            for t in targets.get("direct_reply_targets", []):
                if t.get("phone") == phone:
                    target = t
                    break
            if not target:
                return web.json_response({"error": "Client not found"}, status=404)
            msgs = self._get_session_messages(phone)
            return web.json_response(self._client_summary(target, msgs))
        except Exception:
            logger.exception("Error getting client {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_get_offline_meeting_notes(self, request: web.Request) -> web.Response:
        """GET /api/clients/:phone/offline-meeting-notes — lightweight note index for one client."""
        phone = request.match_info["phone"]
        try:
            notes = self._get_offline_meeting_notes(phone)
            return web.json_response({"notes": notes})
        except ValueError:
            return web.json_response({"error": "Invalid client phone"}, status=400)
        except Exception:
            logger.exception("Error loading offline meeting notes for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_get_offline_meeting_note_detail(self, request: web.Request) -> web.Response:
        """GET /api/clients/:phone/offline-meeting-notes/:noteId — canonical transcript detail."""
        phone = request.match_info["phone"]
        note_id = request.match_info["noteId"]
        try:
            note = self._get_offline_meeting_note_detail(phone, note_id)
            return web.json_response({"note": note})
        except ValueError:
            return web.json_response({"error": "Invalid client phone"}, status=400)
        except LookupError:
            return web.json_response({"error": "Offline meeting note not found"}, status=404)
        except Exception:
            logger.exception("Error loading offline meeting note detail for {} / {}", phone, note_id)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_save_offline_meeting_note(self, request: web.Request) -> web.Response:
        """POST /api/clients/:phone/offline-meeting-note/save — persist one confirmed note row."""
        phone = request.match_info["phone"]
        try:
            try:
                body = await request.json()
            except Exception as exc:
                raise ValueError("Request body must be valid JSON") from exc
            note = self._save_offline_meeting_note(
                phone,
                body.get("transcript"),
                body.get("noteName"),
                body.get("noteId"),
            )
            return web.json_response({"note": note})
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            logger.exception("Error saving offline meeting note for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_transcribe_offline_meeting_note(self, request: web.Request) -> web.Response:
        """POST /api/clients/:phone/offline-meeting-note/transcribe — transcribe one draft."""
        phone = request.match_info["phone"]
        audio_bytes = b""
        try:
            client = self._resolve_client_key(phone)
            audio_bytes, _duration_ms = await self._read_offline_meeting_audio_upload(request)

            google_config = load_google_config()
            provider = GoogleSpeechProvider(google_config)
            transcript = " ".join((await provider.transcribe(audio_bytes)).split()).strip()
            if not transcript:
                return web.json_response(
                    {"error": "未识别到有效语音内容，请重试。"},
                    status=422,
                )

            draft_note_id = generate_offline_meeting_note_id()
            return web.json_response(
                {
                    "noteId": draft_note_id,
                    "noteName": self.session_manager.next_offline_meeting_note_name(client.session_key),
                    "transcript": transcript,
                }
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except GoogleConfigError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        except RuntimeError as exc:
            return web.json_response({"error": str(exc)}, status=502)
        except Exception:
            logger.exception("Error processing offline meeting note for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)
        finally:
            audio_bytes = b""

    async def _handle_delete_client(self, request: web.Request) -> web.Response:
        """DELETE /api/clients/:phone — delete a client session and target."""
        raw_phone = request.match_info["phone"]
        try:
            client = self._resolve_client_key(raw_phone)
            phone = client.phone
            self.session_manager.delete_session(client.session_key)
            self._remove_reply_target(phone)

            await self._broadcast_ws({
                "type": "client_deleted",
                "phone": phone,
            })

            return web.json_response({"status": "deleted", "phone": phone})
        except ValueError:
            return web.json_response({"error": "Invalid client phone"}, status=400)
        except Exception:
            logger.exception("Error deleting client {}", raw_phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_get_messages(self, request: web.Request) -> web.Response:
        """GET /api/messages/:phone — message history for a client."""
        phone = request.match_info["phone"]
        query = getattr(request, "query", {})
        try:
            messages = self._get_session_messages(phone)
            if query.get("format") == "html":
                document = self._render_messages_view_html(phone, messages)
                response = web.Response(text=document, content_type="text/html")
            else:
                response = web.json_response({"messages": messages, "phone": phone})
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response
        except Exception:
            logger.exception("Error getting messages for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_send_message(self, request: web.Request) -> web.Response:
        """POST /api/messages/:phone — send a message as the human agent."""
        phone = request.match_info["phone"]
        try:
            body = await request.json()
            content = body.get("content", "").strip()
            if not content:
                return web.json_response({"error": "Empty message"}, status=400)
            if blocked := self._draft_delivery_disabled_response():
                return blocked

            # 1. Save to session
            key = self._phone_to_session_key(phone)
            session = self.session_manager.get_or_create(key)
            session.add_message("me", content, message_id=f"api_{datetime.now().timestamp()}")
            self._save_whatsapp_session(session)

            # 2. Send via WhatsApp channel
            from nanobot.bus.events import OutboundMessage
            await self.bus.publish_outbound(OutboundMessage(
                channel="whatsapp",
                chat_id=self._resolve_chat_id(phone),
                content=content,
            ))

            client_name = self._journal_client_name(phone)
            await self._append_journal(
                action="SEND_MESSAGE",
                description=f"回复 {client_name}",
                client_id=phone,
                client_name=client_name,
                details={"preview": self._preview_text(content)},
            )

            return web.json_response({"status": "sent", "phone": phone})
        except Exception:
            logger.exception("Error sending message to {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_ai_draft(self, request: web.Request) -> web.Response:
        """POST /api/ai-draft/:phone — request AI to generate a draft reply.

        This uses the agent's process_direct() which runs the full LLM pipeline.
        The response is returned as a draft (not auto-sent).
        """
        phone = request.match_info["phone"]
        try:
            # Notify WS clients that AI is generating
            await self._broadcast_ws({
                "type": "ai_generating",
                "phone": phone,
                "status": "started",
            })
            client_name = self._journal_client_name(phone)
            await self._append_journal(
                action="AI_GENERATE",
                description=f"为 {client_name} 请求 AI 草稿",
                client_id=phone,
                client_name=client_name,
                details={"source": "manual"},
            )

            key = self._phone_to_session_key(phone)
            chat_id = self._resolve_chat_id(phone)

            # Get the latest client message to generate a response for
            session = self.session_manager.read_persisted(key)
            last_client_msg = ""
            for m in reversed(session.messages):
                role = m.get("role", "")
                if role in ("client", "user"):
                    last_client_msg = m.get("content", "")
                    break

            if not last_client_msg:
                await self._broadcast_ws({
                    "type": "ai_generating",
                    "phone": phone,
                    "status": "no_message",
                })
                return web.json_response({"error": "No client message to respond to"}, status=400)

            async def on_progress(text: str) -> None:
                """Stream AI progress to WebSocket."""
                await self._broadcast_ws({
                    "type": "ai_progress",
                    "phone": phone,
                    "content": text,
                })

            # Run agent to generate response
            response = await self.agent.process_direct(
                last_client_msg,
                session_key=key,
                channel="whatsapp",
                chat_id=chat_id,
                on_progress=on_progress,
                persist_history=False,
            )

            if response:
                await self._broadcast_ws({
                    "type": "ai_draft",
                    "phone": phone,
                    "content": response,
                    "status": "completed",
                    "timestamp": datetime.now().isoformat(),
                })
                await self._append_journal(
                    action="AI_DRAFT_READY",
                    description=f"已为 {client_name} 生成 AI 草稿",
                    client_id=phone,
                    client_name=client_name,
                    details={"preview": self._preview_text(response)},
                )

            return web.json_response({
                "status": "completed",
                "phone": phone,
                "draft": response or "",
            })
        except Exception:
            logger.exception("Error generating AI draft for {}", phone)
            await self._broadcast_ws({
                "type": "ai_generating",
                "phone": phone,
                "status": "error",
            })
            return web.json_response({"error": "AI generation failed"}, status=500)

    async def _handle_ai_send(self, request: web.Request) -> web.Response:
        """POST /api/ai-send/:phone — approve and send an AI draft (possibly user-edited) via WhatsApp.

        The session JSONL is written here — NOT during draft generation — so
        only the final, human-approved content is persisted.
        """
        phone = request.match_info["phone"]
        try:
            body = await request.json()
            content = body.get("content", "").strip()
            if not content:
                return web.json_response({"error": "Empty content"}, status=400)
            if blocked := self._draft_delivery_disabled_response():
                return blocked

            # 1. Persist the approved message to session JSONL
            key = self._phone_to_session_key(phone)
            session = self.session_manager.get_or_create(key)
            session.add_message(
                "me", content,
                message_id=f"ai_send_{datetime.now().timestamp()}",
                is_ai_approved=True,
            )
            self._save_whatsapp_session(session)

            # 2. Send via WhatsApp
            from nanobot.bus.events import OutboundMessage
            await self.bus.publish_outbound(OutboundMessage(
                channel="whatsapp",
                chat_id=self._resolve_chat_id(phone),
                content=content,
            ))

            client_name = self._journal_client_name(phone)
            await self._append_journal(
                action="AI_SEND_DRAFT",
                description=f"发送 AI 草稿给 {client_name}",
                client_id=phone,
                client_name=client_name,
                details={"preview": self._preview_text(content)},
            )

            return web.json_response({"status": "sent", "phone": phone})
        except Exception:
            logger.exception("Error sending AI draft to {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_toggle_auto_reply(self, request: web.Request) -> web.Response:
        """PUT /api/auto-reply/:phone — toggle auto-reply for a client."""
        phone = request.match_info["phone"]
        try:
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                return web.json_response({"error": "Missing 'enabled' field"}, status=400)

            data = self._load_reply_targets()
            found = False
            for t in data.get("direct_reply_targets", []):
                if t.get("phone") == phone:
                    t["enabled"] = bool(enabled)
                    found = True
                    break

            if not found:
                return web.json_response({"error": "Client not found"}, status=404)

            self._save_reply_targets(data)

            await self._broadcast_ws({
                "type": "auto_reply_changed",
                "phone": phone,
                "enabled": bool(enabled),
            })

            return web.json_response({"status": "updated", "phone": phone, "enabled": bool(enabled)})
        except Exception:
            logger.exception("Error toggling auto-reply for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_toggle_auto_draft(self, request: web.Request) -> web.Response:
        """PUT /api/auto-draft/:phone — toggle auto-draft for a client."""
        phone = request.match_info["phone"]
        try:
            body = await request.json()
            enabled = body.get("enabled")
            if enabled is None:
                return web.json_response({"error": "Missing 'enabled' field"}, status=400)

            data = self._load_reply_targets()
            found = False
            for t in data.get("direct_reply_targets", []):
                if t.get("phone") == phone:
                    t["auto_draft"] = bool(enabled)
                    # Also make sure the target is enabled in gateway so messages get captured
                    if bool(enabled):
                        t["enabled"] = True
                    found = True
                    break

            if not found:
                return web.json_response({"error": "Client not found"}, status=404)

            self._save_reply_targets(data)

            await self._broadcast_ws({
                "type": "auto_draft_changed",
                "phone": phone,
                "enabled": bool(enabled),
            })
            client_name = self._journal_client_name(phone)
            await self._append_journal(
                action="TOGGLE_AUTO_DRAFT",
                description=f"{ '开启' if bool(enabled) else '关闭' } {client_name} 的自动草稿",
                client_id=phone,
                client_name=client_name,
                details={"enabled": bool(enabled)},
            )

            return web.json_response({"status": "updated", "phone": phone, "autoDraftEnabled": bool(enabled)})
        except Exception:
            logger.exception("Error toggling auto-draft for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_broadcast(self, request: web.Request) -> web.Response:
        """POST /api/broadcast — send a message to multiple clients."""
        try:
            body = await request.json()
            phones = body.get("phones", [])
            content = body.get("content", "").strip()
            if not phones or not content:
                return web.json_response({"error": "Missing phones or content"}, status=400)
            if blocked := self._draft_delivery_disabled_response():
                return blocked

            from nanobot.bus.events import OutboundMessage
            results = []
            for phone in phones:
                chat_id = self._resolve_chat_id(phone)
                await self.bus.publish_outbound(OutboundMessage(
                    channel="whatsapp",
                    chat_id=chat_id,
                    content=content,
                ))
                # Also save to session
                key = self._phone_to_session_key(phone)
                session = self.session_manager.get_or_create(key)
                session.add_message("me", f"[广播] {content}", message_id=f"broadcast_{datetime.now().timestamp()}")
                self._save_whatsapp_session(session)
                results.append({"phone": phone, "status": "sent"})

            await self._append_journal(
                action="BROADCAST",
                description=f"广播消息给 {len(phones)} 位客户",
                details={
                    "phones": phones,
                    "clientNames": [self._journal_client_name(phone) for phone in phones],
                    "preview": self._preview_text(content),
                },
            )

            return web.json_response({"status": "broadcast_sent", "results": results})
        except Exception:
            logger.exception("Error broadcasting")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_sync(self, request: web.Request) -> web.Response:
        """POST /api/sync/:phone — run a confirmed WhatsApp history sync for a client."""
        phone = request.match_info["phone"]
        try:
            whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
            chat_id = self._resolve_chat_id(phone)
            sync_status = self._get_whatsapp_sync_status()
            if not sync_status.get("available", True):
                return web.json_response(
                    {
                        "error": str(sync_status.get("message") or "WhatsApp 历史同步当前不可用。"),
                        "code": "sync_unavailable",
                    },
                    status=503,
                )
            if whatsapp and hasattr(whatsapp, "sync_direct_history"):
                result = await whatsapp.sync_direct_history([phone])
                status = str(result.get("status") or "login_required")
                detail = str(result.get("detail") or "WhatsApp 历史同步失败。")
                if status == "window_launch_failed":
                    status = "sync_unavailable"
                if hasattr(whatsapp, "_set_bridge_status"):
                    bridge_error = status == "bridge_unreachable"
                    whatsapp._set_bridge_status(bridge_error, detail if bridge_error else "")
                    await self._broadcast_current_whatsapp_bridge_status()
                if status != "history_scraped":
                    response_status = 503 if status in {"bridge_unreachable", "sync_unavailable"} else 409
                    return web.json_response(
                        {
                            "error": detail,
                            "code": status,
                        },
                        status=response_status,
                    )

                backend_success = bool(result.get("backend_success"))
                if hasattr(whatsapp, "_set_bridge_status"):
                    whatsapp._set_bridge_status(False, "")
                    await self._broadcast_current_whatsapp_bridge_status()
                response_payload = {
                    "status": "history_scraped",
                    "phone": phone,
                    "matchedEntries": result.get("matched_entries", 0),
                    "importedEntries": result.get("imported_entries", 0),
                    "verifiedEntries": result.get("verified_entries", 0),
                    "verifiedPhones": result.get("verified_phones", []),
                    "backendSuccess": backend_success,
                    "requestId": result.get("request_id"),
                }

                if backend_success:
                    client_name = self._journal_client_name(phone)
                    await self._append_journal(
                        action="SYNC_WHATSAPP",
                        description=f"已同步 {client_name} 的 WhatsApp 历史记录",
                        client_id=phone,
                        client_name=client_name,
                        details={
                            "chatId": chat_id,
                            "matchedEntries": result.get("matched_entries", 0),
                            "importedEntries": result.get("imported_entries", 0),
                            "verifiedEntries": result.get("verified_entries", 0),
                            "verifiedPhones": result.get("verified_phones", []),
                            "requestId": result.get("request_id"),
                        },
                    )

                return web.json_response(response_payload)

            return web.json_response({"error": "WhatsApp channel is not ready."}, status=503)
        except Exception:
            logger.exception("Error triggering sync for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_status(self, _request: web.Request) -> web.Response:
        """GET /api/status — gateway status."""
        try:
            sessions = self.session_manager.list_sessions()
            targets = self._load_reply_targets()
            bridge_status = self._get_whatsapp_bridge_status()
            auth_status = self._get_whatsapp_auth_status()
            sync_status = self._get_whatsapp_sync_status()
            return web.json_response({
                "status": "running",
                "sessions": len(sessions),
                "direct_targets": len(targets.get("direct_reply_targets", [])),
                "group_targets": len(targets.get("group_reply_targets", [])),
                "ws_clients": len(self._ws_clients),
                "channels": list(self.channel_manager.enabled_channels) if self.channel_manager else [],
                "whatsapp_bridge_error": bridge_status.get("error", False),
                "whatsapp_bridge_message": bridge_status.get("message", ""),
                "whatsapp_sync_available": sync_status.get("available", True),
                "whatsapp_sync_message": sync_status.get("message", ""),
                "whatsapp_auth_required": auth_status.get("required"),
                "whatsapp_auth_qr": auth_status.get("qr"),
                "whatsapp_auth_message": auth_status.get("message"),
            })
        except Exception:
            logger.exception("Error getting status")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_bridge_restart(self, _request: web.Request) -> web.Response:
        """POST /api/bridge/restart — kill, rebuild, and restart the bridge process."""
        try:
            # Notify frontend immediately
            await self._broadcast_ws({
                "type": "whatsapp_bridge_status",
                "bridgeError": True,
                "message": "正在重启 Bridge…",
            })

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._restart_bridge_sync)

            if result["status"] == "ok":
                whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
                if whatsapp and hasattr(whatsapp, "_set_bridge_status"):
                    whatsapp._set_bridge_status(False, "")
                await self._broadcast_ws({
                    "type": "whatsapp_bridge_status",
                    "bridgeError": False,
                    "message": "",
                })

            return web.json_response(result)
        except Exception:
            logger.exception("Error restarting bridge")
            return web.json_response({"error": "Internal error"}, status=500)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_chat_id(self, phone: str) -> str:
        """Resolve a phone number to a WhatsApp chat ID."""
        targets = self._load_reply_targets()
        for t in targets.get("direct_reply_targets", []):
            if t.get("phone") == phone:
                return t.get("chat_id") or f"{phone}@s.whatsapp.net"
        return f"{phone}@s.whatsapp.net"

    async def _handle_add_reply_target(self, request: web.Request) -> web.Response:
        """POST /api/reply-targets — add a new direct reply target."""
        try:
            body = await request.json()
            phone = (body.get("phone") or "").strip()
            if not phone:
                return web.json_response({"error": "Missing phone number"}, status=400)

            # Strip leading '+' and any non-digit chars
            phone_clean = "".join(c for c in phone if c.isdigit())
            if not phone_clean or len(phone_clean) < 5:
                return web.json_response({"error": "Invalid phone number"}, status=400)

            label = (body.get("label") or "").strip()
            auto_draft = bool(body.get("autoDraft", True))

            data = self._load_reply_targets()
            # Check if already exists
            for t in data.get("direct_reply_targets", []):
                if t.get("phone") == phone_clean:
                    return web.json_response(
                        {"error": f"Phone {phone_clean} already exists"},
                        status=409,
                    )

            chat_id = f"{phone_clean}@s.whatsapp.net"
            new_target = {
                "phone": phone_clean,
                "enabled": True,
                "auto_draft": auto_draft,
                "label": label,
                "chat_id": chat_id,
                "sender_id": chat_id,
                "push_name": label or f"+{phone_clean}",
                "last_seen_at": datetime.now().isoformat(),
            }

            data.setdefault("direct_reply_targets", []).append(new_target)
            data["source"] = "frontend_add"
            self._save_reply_targets(data)

            # Broadcast to WS so other clients refresh their list
            await self._broadcast_ws({
                "type": "reply_target_added",
                "phone": phone_clean,
                "label": label,
            })
            await self._append_journal(
                action="ADD_REPLY_TARGET",
                description=f"添加新的回复目标 {label or phone_clean}",
                client_id=phone_clean,
                client_name=label or phone_clean,
                details={"phone": phone_clean, "autoDraft": auto_draft},
            )

            logger.info("Added reply target {} (label={!r})", phone_clean, label)
            return web.json_response({
                "status": "added",
                "phone": phone_clean,
                "label": label,
            })
        except web.HTTPException:
            raise
        except Exception:
            logger.exception("Error adding reply target")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_get_journal(self, request: web.Request) -> web.Response:
        """GET /api/journal — recent activity journal entries."""
        try:
            limit_raw = request.query.get("limit", "200")
            limit = max(1, min(1000, int(limit_raw)))
        except ValueError:
            limit = 200

        entries = await self.journal_store.list_entries(limit=limit) if self.journal_store else []
        return web.json_response({"entries": entries})

    async def _handle_add_journal_entry(self, request: web.Request) -> web.Response:
        """POST /api/journal — persist a UI activity entry into the backend journal."""
        try:
            body = await request.json()
            action = str(body.get("action") or "").strip()
            description = str(body.get("description") or "").strip()
            if not action or not description:
                return web.json_response({"error": "Missing action or description"}, status=400)

            entry = await self._append_journal(
                action=action,
                description=description,
                client_id=(body.get("clientId") or None),
                client_name=(body.get("clientName") or None),
                details=body.get("details") if isinstance(body.get("details"), dict) else None,
                user_id=(body.get("userId") or None),
                user_name=(body.get("userName") or None),
                source="frontend",
            )
            return web.json_response({"status": "logged", "entry": entry})
        except Exception:
            logger.exception("Error adding journal entry")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_clear_journal(self, _request: web.Request) -> web.Response:
        """DELETE /api/journal — clear the activity journal."""
        try:
            if self.journal_store:
                await self.journal_store.clear()
            await self._broadcast_ws({"type": "journal_cleared"})
            return web.json_response({"status": "cleared"})
        except Exception:
            logger.exception("Error clearing journal")
            return web.json_response({"error": "Internal error"}, status=500)


# ------------------------------------------------------------------
# Utility functions
# ------------------------------------------------------------------

def _format_time(ts: str) -> str:
    """Format an ISO timestamp to a short display string."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        diff = now - dt
        if diff.days == 0:
            return dt.strftime("%H:%M")
        elif diff.days == 1:
            return "昨天"
        elif diff.days < 7:
            return f"{diff.days}天前"
        return dt.strftime("%m/%d")
    except Exception:
        return ts[:5] if len(ts) >= 5 else ts


def _infer_tags(target: dict, messages: list[dict]) -> list[str]:
    """Infer basic tags for a client from their data and messages."""
    tags = []
    if target.get("enabled"):
        tags.append("自动回复")
    # Check if there are recent messages
    if messages:
        last_role = messages[-1].get("role", "")
        if last_role in ("client", "user"):
            tags.append("待回复")
        # Look for insurance keywords in recent messages
        recent_text = " ".join(m.get("content", "") for m in messages[-5:])
        if any(kw in recent_text for kw in ("重疾", "重大疾病")):
            tags.append("重疾险")
        if any(kw in recent_text for kw in ("医疗", "健康")):
            tags.append("医疗险")
        if any(kw in recent_text for kw in ("理赔", "claim")):
            tags.append("理赔中")
        if any(kw in recent_text for kw in ("家庭", "family", "孩子", "配偶")):
            tags.append("家庭险")
        if any(kw in recent_text for kw in ("牙", "dental", "牙齿")):
            tags.append("牙科险")
    if not tags:
        tags.append("WhatsApp")
    return tags[:4]  # Limit to 4 tags
