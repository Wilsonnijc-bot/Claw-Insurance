"""REST + WebSocket API server for the Nanobot Insurance frontend.

Provides:
- REST endpoints for clients, messages, AI generation, auto-reply, sync
- WebSocket endpoint for real-time updates (new messages, AI progress, status)

Designed to run inside the existing gateway asyncio event loop.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType
from loguru import logger

from nanobot.api.journal import ActivityJournal


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
        self._status_task: asyncio.Task | None = None
        self._bridge_monitor_task: asyncio.Task | None = None
        self._last_browser_status: dict[str, Any] | None = None
        self._last_auth_status: dict[str, Any] | None = None

        self.app = web.Application(middlewares=[self._cors_middleware])
        self._setup_routes()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/clients", self._handle_get_clients)
        self.app.router.add_get("/api/clients/{phone}", self._handle_get_client)
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
        self.app.router.add_post("/api/bridge/check", self._handle_bridge_check)
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
        self._status_task = asyncio.create_task(self._monitor_whatsapp_browser_status())
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

        browser_status = self._get_whatsapp_browser_status()
        if browser_status.get("mode") == "cdp":
            await ws.send_json({
                "type": "whatsapp_browser_status",
                "reusable": browser_status.get("reusable"),
                "message": browser_status.get("message"),
                "mode": browser_status.get("mode"),
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

    def _get_whatsapp_browser_status(self) -> dict[str, Any]:
        """Return the latest WhatsApp Web browser reuse status."""
        whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
        if whatsapp and hasattr(whatsapp, "get_browser_status"):
            try:
                status = whatsapp.get_browser_status()
                if isinstance(status, dict):
                    return status
            except Exception:
                logger.exception("Failed to read WhatsApp browser status")
        return {"mode": None, "reusable": None, "message": ""}

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

    async def _monitor_whatsapp_browser_status(self) -> None:
        """Broadcast WhatsApp browser reuse state changes to UI clients."""
        try:
            while True:
                status = self._get_whatsapp_browser_status()
                comparable = {
                    "mode": status.get("mode"),
                    "reusable": status.get("reusable"),
                    "message": status.get("message"),
                    "severity": status.get("severity", "warning"),
                }
                if comparable != self._last_browser_status:
                    self._last_browser_status = comparable
                    if comparable.get("mode") == "cdp":
                        await self._broadcast_ws({
                            "type": "whatsapp_browser_status",
                            **comparable,
                        })

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
                    if whatsapp and hasattr(whatsapp, "_set_browser_status"):
                        whatsapp._set_browser_status(
                            False,
                            f"Bridge 进程已崩溃 (exit {exit_code})。请点击重启按钮恢复连接。",
                            severity="error",
                        )
                    # Force-broadcast immediately instead of waiting for the 1 s monitor
                    await self._broadcast_ws({
                        "type": "whatsapp_browser_status",
                        "mode": "cdp",
                        "reusable": False,
                        "message": f"Bridge 进程已崩溃 (exit {exit_code})。请点击重启按钮恢复连接。",
                        "severity": "error",
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
            _ensure_whatsapp_cdp_browser,
        )

        # 1. Kill existing bridge
        if self.bridge_proc and self.bridge_proc.poll() is None:
            try:
                _stop_whatsapp_bridge(self.bridge_proc)
            except Exception:
                logger.warning("Failed to stop old bridge — it may have already exited")
            self.bridge_proc = None

        # 2. Ensure CDP Chrome is still alive
        try:
            if self.config.channels.whatsapp.web_browser_mode == "cdp":
                _ensure_whatsapp_cdp_browser(self.config)
        except Exception as e:
            logger.error("Failed to ensure CDP Chrome browser: {}", e)

        # 3. Rebuild bridge from source (invalidates the cache)
        import shutil
        build_dir = Path(__file__).resolve().parents[2] / ".bridge-build"
        if build_dir.exists():
            shutil.rmtree(build_dir)
            logger.info("Deleted stale .bridge-build/ cache")

        bridge_dir = _get_bridge_dir()

        # 4. Restart bridge
        env = _build_whatsapp_bridge_env(self.config)
        proc = subprocess.Popen(
            ["npm", "start"],
            cwd=bridge_dir,
            env=env,
            start_new_session=True,
        )

        # 5. Wait for bridge to become reachable
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

    async def _broadcast_inbound(self, phone: str, content: str, sender: str = "client", **extra) -> None:
        """Notify WebSocket clients about a new inbound message."""
        await self._broadcast_ws({
            "type": "new_message",
            "phone": phone,
            "content": content,
            "sender": sender,
            "timestamp": datetime.now().isoformat(),
            **extra,
        })

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
        """Mirror inbound bus messages to WebSocket and trigger auto-draft.

        When the UI is connected, the MessageBus marks WhatsApp inbound
        messages as capture_only + _auto_draft_candidate.  This task:
        1. Forwards every inbound message to WS so the thread updates live.
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

                # 1. Broadcast the new client message to UI in real time
                await self._broadcast_inbound(
                    phone,
                    content,
                    sender="client",
                    metadata={
                        k: v for k, v in (msg.metadata or {}).items()
                        if not str(k).startswith("_")
                    },
                )
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

            # Snapshot session length so we can roll back
            session = self.session_manager.get_or_create(key)
            snapshot_len = len(session.messages)

            response = await self.agent.process_direct(
                client_message,
                session_key=key,
                channel="whatsapp",
                chat_id=chat_id,
            )

            # Roll back — only persist when user actually sends
            session = self.session_manager.get_or_create(key)
            if len(session.messages) > snapshot_len:
                session.messages = session.messages[:snapshot_len]
                self.session_manager.save(session)

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
        p = self._reply_targets_path()
        if not p.exists():
            return {"direct_reply_targets": [], "group_reply_targets": []}
        return json.loads(p.read_text(encoding="utf-8"))

    def _save_reply_targets(self, data: dict) -> None:
        p = self._reply_targets_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = datetime.now().isoformat()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _resolve_client_key(self, phone: str) -> "ClientKey":
        """Derive a validated ClientKey from a raw phone string."""
        from nanobot.session.client_key import ClientKey
        return ClientKey.normalize(phone)

    def _phone_to_session_key(self, phone: str) -> str:
        return self._resolve_client_key(phone).session_key

    def _get_session_messages(self, phone: str) -> list[dict]:
        """Return visible messages for a WhatsApp phone session."""
        client = self._resolve_client_key(phone)
        session = self.session_manager.get_for_client(client)
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
            messages.append({
                "id": m.get("message_id", m.get("timestamp", "")),
                "role": role,
                "sender": "client" if role == "client" else ("ai" if m.get("is_ai_draft") else "agent"),
                "content": content,
                "timestamp": m.get("timestamp", ""),
                "isAIDraft": bool(m.get("is_ai_draft")),
                "fromMe": role in ("me", "assistant"),
            })
        return messages

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

    async def _handle_get_messages(self, request: web.Request) -> web.Response:
        """GET /api/messages/:phone — message history for a client."""
        phone = request.match_info["phone"]
        try:
            messages = self._get_session_messages(phone)
            return web.json_response({"messages": messages, "phone": phone})
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

            # 1. Save to session
            key = self._phone_to_session_key(phone)
            session = self.session_manager.get_or_create(key)
            session.add_message("me", content, message_id=f"api_{datetime.now().timestamp()}")
            self.session_manager.save(session)

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
            session = self.session_manager.get_or_create(key)
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

            # Snapshot session length before generation so we can roll back
            session_snapshot_len = len(session.messages)

            # Run agent to generate response
            response = await self.agent.process_direct(
                last_client_msg,
                session_key=key,
                channel="whatsapp",
                chat_id=chat_id,
                on_progress=on_progress,
            )

            # Roll back: remove messages added by process_direct so the
            # session JSONL only records what is actually sent.  The user
            # may edit the draft — we persist the *final* version in
            # _handle_ai_send when they click Send.
            session = self.session_manager.get_or_create(key)
            if len(session.messages) > session_snapshot_len:
                session.messages = session.messages[:session_snapshot_len]
                self.session_manager.save(session)
                logger.debug("Rolled back draft from session {} (kept {} msgs)", key, session_snapshot_len)

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

            # 1. Persist the approved message to session JSONL
            key = self._phone_to_session_key(phone)
            session = self.session_manager.get_or_create(key)
            session.add_message(
                "me", content,
                message_id=f"ai_send_{datetime.now().timestamp()}",
                is_ai_approved=True,
            )
            self.session_manager.save(session)

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
                self.session_manager.save(session)
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
            browser_status = self._get_whatsapp_browser_status()
            if browser_status.get("mode") == "cdp" and whatsapp and hasattr(whatsapp, "check_browser_status"):
                browser_status = await whatsapp.check_browser_status()
                if not browser_status.get("reusable"):
                    return web.json_response(
                        {
                            "error": browser_status.get("detail") or "WhatsApp Web 历史同步不可用。",
                            "code": browser_status.get("status") or "scrape_not_ready",
                            "whatsapp_browser_reusable": False,
                            "whatsapp_browser_severity": browser_status.get("severity", "warning"),
                        },
                        status=409,
                    )
            chat_id = self._resolve_chat_id(phone)
            if whatsapp and hasattr(whatsapp, "sync_direct_history"):
                result = await whatsapp.sync_direct_history([phone])
                status = str(result.get("status") or "not_ready")
                if status != "history_scraped":
                    response_status = 504 if status == "sync_timeout" else 409
                    return web.json_response(
                        {
                            "error": result.get("detail") or "WhatsApp 历史同步失败。",
                            "code": status,
                            "whatsapp_browser_reusable": False,
                            "whatsapp_browser_severity": result.get("severity", "warning"),
                        },
                        status=response_status,
                    )

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
                    },
                )
                return web.json_response(
                    {
                        "status": "history_scraped",
                        "phone": phone,
                        "matchedEntries": result.get("matched_entries", 0),
                        "importedEntries": result.get("imported_entries", 0),
                    }
                )

            return web.json_response({"error": "WhatsApp channel is not ready."}, status=503)
        except Exception:
            logger.exception("Error triggering sync for {}", phone)
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_status(self, _request: web.Request) -> web.Response:
        """GET /api/status — gateway status."""
        try:
            sessions = self.session_manager.list_sessions()
            targets = self._load_reply_targets()
            browser_status = self._get_whatsapp_browser_status()
            auth_status = self._get_whatsapp_auth_status()
            return web.json_response({
                "status": "running",
                "sessions": len(sessions),
                "direct_targets": len(targets.get("direct_reply_targets", [])),
                "group_targets": len(targets.get("group_reply_targets", [])),
                "ws_clients": len(self._ws_clients),
                "channels": list(self.channel_manager.enabled_channels) if self.channel_manager else [],
                "whatsapp_browser_mode": browser_status.get("mode"),
                "whatsapp_browser_reusable": browser_status.get("reusable"),
                "whatsapp_browser_message": browser_status.get("message"),
                "whatsapp_browser_severity": browser_status.get("severity", "warning"),
                "whatsapp_auth_required": auth_status.get("required"),
                "whatsapp_auth_qr": auth_status.get("qr"),
                "whatsapp_auth_message": auth_status.get("message"),
            })
        except Exception:
            logger.exception("Error getting status")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_bridge_check(self, _request: web.Request) -> web.Response:
        """POST /api/bridge/check — run a one-shot WhatsApp Web scrape readiness check."""
        try:
            whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
            if whatsapp and hasattr(whatsapp, "check_browser_status"):
                status = await whatsapp.check_browser_status()
            else:
                status = self._get_whatsapp_browser_status()
            return web.json_response({
                "status": status.get("status") or "checked",
                "reusable": status.get("reusable"),
                "message": status.get("detail") or status.get("message"),
                "severity": status.get("severity", "warning"),
            })
        except Exception:
            logger.exception("Error checking bridge status")
            return web.json_response({"error": "Internal error"}, status=500)

    async def _handle_bridge_restart(self, _request: web.Request) -> web.Response:
        """POST /api/bridge/restart — kill, rebuild, and restart the bridge process."""
        try:
            # Notify frontend immediately
            await self._broadcast_ws({
                "type": "whatsapp_browser_status",
                "mode": "cdp",
                "reusable": False,
                "message": "正在重启 Bridge…",
                "severity": "warning",
            })

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._restart_bridge_sync)

            if result["status"] == "ok":
                # Bridge is back — refresh the one-shot scrape readiness cache
                whatsapp = self.channel_manager.get_channel("whatsapp") if self.channel_manager else None
                if whatsapp and hasattr(whatsapp, "check_browser_status"):
                    # Give WS reconnect a moment
                    await asyncio.sleep(2)
                    await whatsapp.check_browser_status()

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
