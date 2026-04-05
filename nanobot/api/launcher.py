"""Launcher server — a lightweight HTTP server that starts the full Nanobot gateway on login.

Flow:
1. `nanobot launcher` starts this server on port 3456.
2. Before login: only /api/login, /api/status, /ws work. Other routes return 503.
3. Frontend calls POST /api/login → gateway starts in-process (bridge, agent, channels).
4. Once ready, all API endpoints become available (proxied to the internal ApiServer).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from aiohttp import web, WSMsgType
from loguru import logger

from nanobot.api.journal import ActivityJournal


class LauncherServer:
    """Lightweight aiohttp server that bootstraps the full Nanobot gateway on demand."""

    def __init__(self, *, config: Any, api_port: int = 3456):
        self.config = config
        self.api_port = api_port
        self._journal_store = ActivityJournal(config.workspace_path)

        # Gateway state
        self._gateway_ready = False
        self._gateway_starting = False
        self._gateway_error: str | None = None

        # Gateway components (populated after login)
        self._api_server: Any = None  # The real ApiServer
        self._bridge_proc: Any = None
        self._privacy_proc: Any = None
        self._bus: Any = None
        self._agent: Any = None
        self._channels: Any = None
        self._cron: Any = None
        self._heartbeat: Any = None
        self._session_manager: Any = None
        self._gateway_tasks: list[asyncio.Task] = []

        # Pre-gateway WS clients (before ApiServer takes over)
        self._ws_clients: set[web.WebSocketResponse] = set()

        # HTTP server
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        self.app = web.Application(middlewares=[self._cors_middleware])
        self._setup_routes()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _setup_routes(self) -> None:
        # Login + status are always available
        self.app.router.add_post("/api/login", self._handle_login)
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/ws", self._handle_ws)

        # All other /api/* routes — proxy to real ApiServer or 503
        self.app.router.add_get("/api/clients", self._proxy)
        self.app.router.add_get("/api/clients/{phone}", self._proxy)
        self.app.router.add_get("/api/messages/{phone}", self._proxy)
        self.app.router.add_post("/api/messages/{phone}", self._proxy)
        self.app.router.add_post("/api/ai-draft/{phone}", self._proxy)
        self.app.router.add_post("/api/ai-send/{phone}", self._proxy)
        self.app.router.add_put("/api/auto-reply/{phone}", self._proxy)
        self.app.router.add_put("/api/auto-draft/{phone}", self._proxy)
        self.app.router.add_post("/api/reply-targets", self._proxy)
        self.app.router.add_post("/api/broadcast", self._proxy)
        self.app.router.add_post("/api/sync/{phone}", self._proxy)
        self.app.router.add_post("/api/bridge/check", self._proxy)
        self.app.router.add_post("/api/bridge/restart", self._proxy)
        self.app.router.add_get("/api/journal", self._proxy)
        self.app.router.add_post("/api/journal", self._proxy)
        self.app.router.add_delete("/api/journal", self._proxy)

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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the launcher HTTP server."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "0.0.0.0", self.api_port)
        await self._site.start()
        logger.info("Launcher server running on http://0.0.0.0:{}", self.api_port)

    async def stop(self) -> None:
        """Stop everything — gateway components + HTTP server."""
        # Stop gateway components
        if self._api_server:
            if self._api_server._outbound_task:
                self._api_server._outbound_task.cancel()
                try:
                    await self._api_server._outbound_task
                except asyncio.CancelledError:
                    pass
            if self._api_server._inbound_task:
                self._api_server._inbound_task.cancel()
                try:
                    await self._api_server._inbound_task
                except asyncio.CancelledError:
                    pass
        for task in self._gateway_tasks:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self._agent:
            await self._agent.close_mcp()
            self._agent.stop()
        if self._heartbeat:
            self._heartbeat.stop()
        if self._cron:
            self._cron.stop()
        if self._channels:
            await self._channels.stop_all()
        if self._bridge_proc:
            try:
                from nanobot.cli.commands import _stop_whatsapp_bridge
                _stop_whatsapp_bridge(self._bridge_proc)
            except Exception:
                pass
        if self._privacy_proc and self._privacy_proc.poll() is None:
            self._privacy_proc.terminate()

        # Close WS clients
        for ws in list(self._ws_clients):
            await ws.close()

        # Stop HTTP server
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        logger.info("Launcher server stopped")

    # ------------------------------------------------------------------
    # Pre-gateway handlers
    # ------------------------------------------------------------------

    async def _handle_login(self, request: web.Request) -> web.Response:
        """POST /api/login — start the gateway."""
        if self._gateway_ready:
            return web.json_response({
                "status": "ok",
                "message": "Gateway already running",
                "gateway_ready": True,
            })

        if self._gateway_starting:
            return web.json_response({
                "status": "starting",
                "message": "Gateway is starting up, please wait...",
                "gateway_ready": False,
            })

        try:
            body = await request.json()
        except Exception:
            body = {}
        username = body.get("username", "agent")

        logger.info("Login from '{}' — starting gateway...", username)
        await self._journal_store.log(
            action="LOGIN",
            description=f"{username} 登录系统",
            user_name=username,
            source="backend",
        )
        self._gateway_starting = True
        self._gateway_error = None

        # Start gateway in background — don't block the HTTP response
        asyncio.create_task(self._start_gateway())

        return web.json_response({
            "status": "starting",
            "message": "Gateway is starting up...",
            "gateway_ready": False,
            "username": username,
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """GET /api/status — launcher + gateway readiness check."""
        if self._gateway_ready and self._api_server:
            try:
                return await self._api_server._handle_status(request)
            except Exception:
                pass

        return web.json_response({
            "status": "launcher",
            "gateway_ready": self._gateway_ready,
            "gateway_starting": self._gateway_starting,
            "gateway_error": self._gateway_error,
        })

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        """WebSocket — proxy to real ApiServer if ready, else simple keepalive."""
        if self._gateway_ready and self._api_server:
            return await self._api_server._handle_ws(request)

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.add(ws)

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if data.get("type") == "ping":
                            await ws.send_json({
                                "type": "pong",
                                "gateway_ready": self._gateway_ready,
                                "gateway_starting": self._gateway_starting,
                            })
                    except Exception:
                        pass
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            self._ws_clients.discard(ws)

        return ws

    async def _proxy(self, request: web.Request) -> web.Response:
        """Proxy an API request to the real ApiServer handler."""
        if not self._gateway_ready or not self._api_server:
            return web.json_response(
                {"error": "Gateway not ready. Please login first.", "gateway_ready": False},
                status=503,
            )

        # Look up the matching handler in the real ApiServer
        path = request.path
        method = request.method
        handler_map = self._get_handler_map()

        # Try exact match first
        handler = handler_map.get((method, path))

        if not handler:
            # Try parameterized route matching
            for (m, pattern), h in handler_map.items():
                if m != method:
                    continue
                if "{phone}" in pattern:
                    prefix = pattern.split("{phone}")[0]
                    suffix = pattern.split("{phone}")[1] if len(pattern.split("{phone}")) > 1 else ""
                    if path.startswith(prefix) and path.endswith(suffix):
                        handler = h
                        break

        if handler:
            return await handler(request)

        return web.json_response({"error": "Not found"}, status=404)

    def _get_handler_map(self) -> dict[tuple[str, str], Any]:
        """Map (method, path_pattern) → handler from the real ApiServer."""
        if not self._api_server:
            return {}
        s = self._api_server
        return {
            ("GET", "/api/clients"): s._handle_get_clients,
            ("GET", "/api/clients/{phone}"): s._handle_get_client,
            ("GET", "/api/messages/{phone}"): s._handle_get_messages,
            ("POST", "/api/messages/{phone}"): s._handle_send_message,
            ("POST", "/api/ai-draft/{phone}"): s._handle_ai_draft,
            ("POST", "/api/ai-send/{phone}"): s._handle_ai_send,
            ("PUT", "/api/auto-reply/{phone}"): s._handle_toggle_auto_reply,
            ("PUT", "/api/auto-draft/{phone}"): s._handle_toggle_auto_draft,
            ("POST", "/api/reply-targets"): s._handle_add_reply_target,
            ("POST", "/api/broadcast"): s._handle_broadcast,
            ("POST", "/api/sync/{phone}"): s._handle_sync,
            ("GET", "/api/journal"): s._handle_get_journal,
            ("POST", "/api/journal"): s._handle_add_journal_entry,
            ("DELETE", "/api/journal"): s._handle_clear_journal,
        }

    # ------------------------------------------------------------------
    # Gateway bootstrap
    # ------------------------------------------------------------------

    async def _start_gateway(self) -> None:
        """Start the full Nanobot gateway in-process (called after login)."""
        try:
            from nanobot.agent.loop import AgentLoop
            from nanobot.api.server import ApiServer
            from nanobot.bus.queue import MessageBus
            from nanobot.channels.manager import ChannelManager
            from nanobot.cli.commands import (
                _make_provider,
                _maybe_enable_privacy_gateway,
                _start_whatsapp_bridge,
            )
            from nanobot.utils.helpers import sync_workspace_templates
            from nanobot.cron.service import CronService
            from nanobot.cron.types import CronJob
            from nanobot.heartbeat.service import HeartbeatService
            from nanobot.session.manager import SessionManager

            config = self.config

            await self._broadcast_ws({"type": "gateway_status", "status": "starting_bridge"})

            # 1. Start WhatsApp bridge (includes CDP browser)
            logger.info("Starting WhatsApp bridge + CDP browser...")
            self._bridge_proc = _start_whatsapp_bridge(config)
            self._privacy_proc = _maybe_enable_privacy_gateway(config)

            await self._broadcast_ws({"type": "gateway_status", "status": "initializing_core"})

            # 2. Initialize core components
            sync_workspace_templates(config.workspace_path)
            self._bus = MessageBus()
            provider = _make_provider(config)
            self._session_manager = SessionManager(config.workspace_path)

            cron_store_path = config.workspace_path / "cron" / "jobs.json"
            self._cron = CronService(cron_store_path)

            self._agent = AgentLoop(
                bus=self._bus,
                provider=provider,
                workspace=config.workspace_path,
                model=config.agents.defaults.model,
                temperature=config.agents.defaults.temperature,
                max_tokens=config.agents.defaults.max_tokens,
                max_iterations=config.agents.defaults.max_tool_iterations,
                memory_window=config.agents.defaults.memory_window,
                reasoning_effort=config.agents.defaults.reasoning_effort,
                brave_api_key=config.tools.web.search.api_key or None,
                web_proxy=config.tools.web.proxy or None,
                exec_config=config.tools.exec,
                cron_service=self._cron,
                restrict_to_workspace=config.tools.restrict_to_workspace,
                session_manager=self._session_manager,
                mcp_servers=config.tools.mcp_servers,
                channels_config=config.channels,
                privacy_config=config.privacy_gateway,
            )

            agent = self._agent
            cron = self._cron
            bus = self._bus

            # Cron callback
            async def on_cron_job(job: CronJob) -> str | None:
                from nanobot.agent.tools.cron import CronTool
                from nanobot.agent.tools.message import MessageTool
                reminder_note = (
                    "[Scheduled Task] Timer finished.\n\n"
                    f"Task '{job.name}' has been triggered.\n"
                    f"Scheduled instruction: {job.payload.message}"
                )
                cron_tool = agent.tools.get("cron")
                cron_token = None
                if isinstance(cron_tool, CronTool):
                    cron_token = cron_tool.set_cron_context(True)
                try:
                    response = await agent.process_direct(
                        reminder_note,
                        session_key=f"cron:{job.id}",
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to or "direct",
                    )
                finally:
                    if isinstance(cron_tool, CronTool) and cron_token is not None:
                        cron_tool.reset_cron_context(cron_token)
                message_tool = agent.tools.get("message")
                if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
                    return response
                if job.payload.deliver and job.payload.to and response:
                    from nanobot.bus.events import OutboundMessage
                    await bus.publish_outbound(OutboundMessage(
                        channel=job.payload.channel or "cli",
                        chat_id=job.payload.to,
                        content=response,
                    ))
                return response

            cron.on_job = on_cron_job

            # Channel manager
            self._channels = ChannelManager(config, bus)

            # Heartbeat
            def _pick_heartbeat_target() -> tuple[str, str]:
                enabled = set(self._channels.enabled_channels)
                for item in self._session_manager.list_sessions():
                    key = item.get("key") or ""
                    if ":" not in key:
                        continue
                    channel, chat_id = key.split(":", 1)
                    if channel in {"cli", "system"}:
                        continue
                    if channel in enabled and chat_id:
                        return channel, chat_id
                return "cli", "direct"

            async def on_heartbeat_execute(tasks: str) -> str:
                channel, chat_id = _pick_heartbeat_target()
                async def _silent(*_a, **_kw):
                    pass
                return await agent.process_direct(
                    tasks, session_key="heartbeat",
                    channel=channel, chat_id=chat_id, on_progress=_silent,
                )

            async def on_heartbeat_notify(response: str) -> None:
                from nanobot.bus.events import OutboundMessage
                channel, chat_id = _pick_heartbeat_target()
                if channel == "cli":
                    return
                await bus.publish_outbound(OutboundMessage(
                    channel=channel, chat_id=chat_id, content=response,
                ))

            hb_cfg = config.gateway.heartbeat
            self._heartbeat = HeartbeatService(
                workspace=config.workspace_path,
                provider=provider,
                model=agent.model,
                on_execute=on_heartbeat_execute,
                on_notify=on_heartbeat_notify,
                interval_s=hb_cfg.interval_s,
                enabled=hb_cfg.enabled,
            )

            await self._broadcast_ws({"type": "gateway_status", "status": "starting_services"})

            # 3. Create ApiServer (without binding to port — we proxy to it)
            self._api_server = ApiServer(
                config=config,
                bus=bus,
                session_manager=self._session_manager,
                agent=agent,
                channel_manager=self._channels,
                journal_store=self._journal_store,
                bridge_proc=self._bridge_proc,
            )
            # Start mirror tasks (outbound + inbound listeners) without HTTP binding
            self._api_server._outbound_task = asyncio.create_task(
                self._api_server._mirror_outbound()
            )
            self._api_server._inbound_task = asyncio.create_task(
                self._api_server._mirror_inbound()
            )
            # Start background monitors (browser status + bridge process health)
            self._api_server._status_task = asyncio.create_task(
                self._api_server._monitor_whatsapp_browser_status()
            )
            self._api_server._bridge_monitor_task = asyncio.create_task(
                self._api_server._monitor_bridge_process()
            )

            # 4. Start services
            await self._cron.start()
            await self._heartbeat.start()

            agent_task = asyncio.create_task(agent.run())
            channels_task = asyncio.create_task(self._channels.start_all())
            self._gateway_tasks = [agent_task, channels_task]

            # 5. Mark ready
            self._gateway_ready = True
            self._gateway_starting = False
            logger.info("Gateway started successfully — all API endpoints now available")

            await self._broadcast_ws({"type": "gateway_status", "status": "ready"})

        except Exception as e:
            logger.exception("Failed to start gateway")
            self._gateway_error = str(e)
            self._gateway_starting = False
            await self._broadcast_ws({
                "type": "gateway_status",
                "status": "error",
                "error": str(e),
            })

    async def _broadcast_ws(self, event: dict) -> None:
        """Broadcast to pre-gateway WS clients."""
        if not self._ws_clients:
            return
        data = json.dumps(event, default=str)
        closed = []
        for ws in self._ws_clients:
            try:
                await ws.send_str(data)
            except Exception:
                closed.append(ws)
        for ws in closed:
            self._ws_clients.discard(ws)
