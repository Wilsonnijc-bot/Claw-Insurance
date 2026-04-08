"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
import mimetypes
from dataclasses import dataclass, field
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

from loguru import logger

from nanobot.bus.events import HistoryImportResult, InboundHistoryBatch, InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.channels.whatsapp_contacts import (
    WhatsAppContact,
    find_contact,
    has_local_store,
    load_contacts,
    normalize_contact_id,
)
from nanobot.channels.whatsapp_group_members import (
    WhatsAppGroupMember,
    normalize_member_id,
)
from nanobot.channels.whatsapp_reply_targets import (
    DirectReplyTarget,
    GroupReplyTarget,
    find_direct_reply_target,
    init_reply_targets_store,
    load_direct_reply_targets,
    load_group_reply_targets,
    match_direct_reply_target,
    match_group_reply_target,
    observe_direct_identification,
    observe_group_identification,
    reply_targets_path,
)
from nanobot.config.schema import WhatsAppConfig
from nanobot.utils.helpers import get_workspace_path


@dataclass
class _HistoryParseRequest:
    kind: str  # "manual" | "bulk"
    targets: list[dict]
    scope_phones: list[str]
    future: asyncio.Future | None = None
    timeout_s: float = 30.0
    source: str = ""
    description: str = ""
    extra_waiters: list[asyncio.Future] = field(default_factory=list)


class WhatsAppChannel(BaseChannel):
    """
    WhatsApp channel that connects to a Node.js bridge.

    The bridge uses @whiskeysockets/baileys to handle the WhatsApp Web protocol.
    Communication between Python and Node.js is via WebSocket.
    """

    name = "whatsapp"

    def __init__(self, config: WhatsAppConfig, bus: MessageBus, workspace: Path | None = None):
        super().__init__(config, bus)
        self.config: WhatsAppConfig = config
        self._workspace = get_workspace_path(str(workspace) if workspace else None)
        from nanobot.utils.paths import project_root
        self._project_root = project_root()
        self._reply_targets_file = reply_targets_path(self.config.reply_targets_file, self._project_root)
        init_reply_targets_store(self._reply_targets_file)
        self._ws = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._history_cache: OrderedDict[str, dict] = OrderedDict()
        self._auth_required: bool = False
        self._auth_qr: str = ""
        self._auth_message: str = ""
        self._pending_history_sync_acks: dict[str, asyncio.Future] = {}
        self._ws_reconnect_failures: int = 0
        self._ws_max_reconnect_failures: int = 12  # 12 * 5s = 60s before escalating
        self._bridge_error: bool = False
        self._bridge_message: str = ""
        self._parse_queue_event = asyncio.Event()
        self._manual_parse_queue: OrderedDict[str, _HistoryParseRequest] = OrderedDict()
        self._bulk_parse_request: _HistoryParseRequest | None = None
        self._parse_task: asyncio.Task | None = None
        self._active_manual_phone: str | None = None
        self._active_request: _HistoryParseRequest | None = None

    def get_bridge_status(self) -> dict[str, object]:
        """Return the latest bridge-process status relevant to the UI."""
        return {
            "error": self._bridge_error,
            "message": self._bridge_message,
        }

    def get_auth_status(self) -> dict[str, object]:
        """Return the latest known Baileys authentication status."""
        return {
            "required": self._auth_required,
            "qr": self._auth_qr,
            "message": self._auth_message,
        }

    def _set_auth_status(self, required: bool, qr: str = "", message: str = "") -> None:
        self._auth_required = bool(required)
        self._auth_qr = str(qr or "").strip()
        self._auth_message = str(message or "").strip()

    def _set_bridge_status(self, error: bool, message: str = "") -> None:
        self._bridge_error = bool(error)
        self._bridge_message = str(message or "").strip()

    async def sync_direct_history(self, phones: list[str] | None = None, timeout_s: float = 30.0) -> dict[str, object]:
        """Run a manual direct-history parse for one client."""
        scope_phones = self._normalize_direct_history_scope(phones)
        targets = self._build_scoped_direct_history_targets_payload(scope_phones)
        if not targets:
            return {
                "status": "chat_not_found",
                "detail": "No enabled WhatsApp direct target is configured for this client.",
            }
        phone = scope_phones[0]
        return await self._enqueue_manual_history_parse(phone, targets[0], timeout_s=timeout_s)

    async def parse_reply_targets_once(self, timeout_s: float = 90.0) -> dict[str, object]:
        """Run one bulk sidebar parse for all enabled direct reply targets."""
        targets = self._build_scoped_direct_history_targets_payload()
        phones = [
            phone for phone in self._normalize_direct_history_scope(
                [str(target.get("phone", "") or "") for target in targets]
            )
        ]
        if not targets:
            return {
                "status": "history_scraped",
                "detail": "No enabled WhatsApp direct reply targets were configured.",
                "requested_targets": 0,
                "scraped_targets": 0,
                "missed_targets": 0,
                "matched_entries": 0,
                "imported_entries": 0,
            }

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        if not self._running:
            request = _HistoryParseRequest(
                kind="bulk",
                targets=targets,
                scope_phones=phones,
                timeout_s=timeout_s,
                source="login",
                description="bulk parse on login",
            )
            return await self._run_history_parse_request(request)
        active_request = getattr(self, "_active_request", None)
        if active_request is not None and active_request.kind == "bulk":
            active_request.extra_waiters.append(future)
            return await future
        if self._bulk_parse_request and self._bulk_parse_request.future and not self._bulk_parse_request.future.done():
            return await self._bulk_parse_request.future

        self._bulk_parse_request = _HistoryParseRequest(
            kind="bulk",
            targets=targets,
            scope_phones=phones,
            future=future,
            timeout_s=timeout_s,
            source="login",
            description="bulk parse on login",
        )
        self._parse_queue_event.set()
        return await future

    async def _enqueue_manual_history_parse(self, phone: str, target: dict, timeout_s: float = 30.0) -> dict[str, object]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        if not self._running:
            request = _HistoryParseRequest(
                kind="manual",
                targets=[target],
                scope_phones=[phone],
                timeout_s=timeout_s,
                source="ui",
                description=f"manual parse for {phone}",
            )
            return await self._run_history_parse_request(request)

        active_request = getattr(self, "_active_request", None)
        if (
            active_request is not None
            and active_request.kind == "manual"
            and phone in active_request.scope_phones
        ):
            active_request.extra_waiters.append(future)
            return await future

        pending = self._manual_parse_queue.get(phone)
        if pending is not None:
            pending.extra_waiters.append(future)
            return await future

        self._manual_parse_queue[phone] = _HistoryParseRequest(
            kind="manual",
            targets=[target],
            scope_phones=[phone],
            future=future,
            timeout_s=timeout_s,
            source="ui",
            description=f"manual parse for {phone}",
        )
        self._parse_queue_event.set()
        return await future

    async def _history_parse_worker(self) -> None:
        while self._running:
            await self._parse_queue_event.wait()
            while self._running:
                request = self._dequeue_history_parse_request()
                if request is None:
                    self._parse_queue_event.clear()
                    break

                self._active_request = request
                try:
                    result = await self._run_history_parse_request(request)
                except Exception as e:
                    logger.exception("WhatsApp history parse worker failed")
                    result = {
                        "status": "window_launch_failed",
                        "detail": str(e),
                    }
                finally:
                    self._active_request = None

                waiters = [request.future, *request.extra_waiters]
                for waiter in waiters:
                    if waiter is not None and not waiter.done():
                        waiter.set_result(result)

    def _dequeue_history_parse_request(self) -> _HistoryParseRequest | None:
        if self._manual_parse_queue:
            phone, request = self._manual_parse_queue.popitem(last=False)
            self._active_manual_phone = phone
            return request
        if self._bulk_parse_request is not None:
            request = self._bulk_parse_request
            self._bulk_parse_request = None
            self._active_manual_phone = None
            return request
        self._active_manual_phone = None
        return None

    async def _run_history_parse_request(self, request: _HistoryParseRequest) -> dict[str, object]:
        scope_phones = self._normalize_direct_history_scope(request.scope_phones)
        await self._replay_cached_history(scope_phones)

        if not await self._wait_for_bridge_connection(timeout_s=min(max(request.timeout_s, 1.0), 30.0)):
            return {
                "status": "bridge_unreachable",
                "detail": "Bridge 连接中断，无法执行 WhatsApp 历史同步。",
            }

        request_id = uuid4().hex
        observer = self.bus.add_history_result_observer()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(request.timeout_s, 1.0)
        ack_future: asyncio.Future = loop.create_future()
        self._pending_history_sync_acks[request_id] = ack_future

        try:
            if request.kind == "manual":
                payload = {
                    "type": "scrape_direct_history",
                    "target": request.targets[0],
                    "requestId": request_id,
                }
            else:
                payload = {
                    "type": "scrape_reply_targets_history",
                    "targets": request.targets,
                    "requestId": request_id,
                }

            await self._ws.send(json.dumps(payload, ensure_ascii=False))

            ack_timeout = max(0.1, deadline - loop.time())
            ack = await asyncio.wait_for(ack_future, timeout=ack_timeout)
            status = str(ack.get("status", "") or "").strip() or "login_required"
            detail = str(ack.get("detail", "") or "").strip()
            scraped_targets = int(ack.get("scrapedTargets") or 0)
            scraped_messages = int(ack.get("scrapedMessages") or 0)
            missed_targets = int(ack.get("missedTargets") or 0)
            import_phones = self._normalize_direct_history_scope(ack.get("importPhones"))

            if status != "history_scraped":
                return {
                    "status": status,
                    "detail": detail or "WhatsApp 历史同步失败。",
                }

            if scraped_targets <= 0 and request.kind == "manual":
                return {
                    "status": "chat_not_found",
                    "detail": detail or "未找到该客户的 WhatsApp 聊天窗口。",
                }

            if not import_phones:
                return {
                    "status": "history_scraped",
                    "detail": "WhatsApp 历史同步完成。",
                    "requested_targets": len(request.targets),
                    "scraped_targets": scraped_targets,
                    "missed_targets": missed_targets,
                    "matched_entries": 0,
                    "imported_entries": 0,
                    "request_id": request_id,
                }

            import_timeout = max(0.1, deadline - loop.time())
            import_result = await asyncio.wait_for(
                self._wait_for_history_import_results(observer, request_id, import_phones),
                timeout=import_timeout,
            )
            return {
                "status": "history_scraped",
                "detail": "WhatsApp 历史同步完成。",
                "requested_targets": len(request.targets),
                "scraped_targets": scraped_targets,
                "missed_targets": missed_targets,
                "matched_entries": int(import_result.matched_entries),
                "imported_entries": int(import_result.imported_entries),
                "request_id": request_id,
            }
        except asyncio.TimeoutError:
            return {
                "status": "bridge_unreachable",
                "detail": "等待 WhatsApp 历史同步结果超时，请重试。",
            }
        finally:
            self.bus.remove_history_result_observer(observer)
            pending = self._pending_history_sync_acks.pop(request_id, None)
            if pending is not None and not pending.done():
                pending.cancel()

    async def _wait_for_history_import_results(
        self,
        observer: asyncio.Queue[HistoryImportResult],
        request_id: str,
        phones: list[str] | None = None,
    ) -> HistoryImportResult:
        requested_phones = set(self._normalize_direct_history_scope(phones))
        aggregate = HistoryImportResult(channel=self.name, metadata={"request_id": request_id})
        while True:
            result = await observer.get()
            if result.channel != self.name:
                continue
            metadata = result.metadata or {}
            if str(metadata.get("request_id", "") or "").strip() != request_id:
                continue
            aggregate.matched_entries += int(result.matched_entries)
            aggregate.imported_entries += int(result.imported_entries)
            merged = set(aggregate.phones)
            merged.update(self._normalize_direct_history_scope(result.phones))
            aggregate.phones = sorted(merged)
            if not requested_phones:
                return aggregate
            imported_phones = set(aggregate.phones)
            if requested_phones.issubset(imported_phones):
                return aggregate

    async def _wait_for_bridge_connection(self, timeout_s: float = 30.0) -> bool:
        if self._connected and self._ws:
            return True
        deadline = asyncio.get_running_loop().time() + max(timeout_s, 0.1)
        while asyncio.get_running_loop().time() < deadline:
            if self._connected and self._ws:
                return True
            await asyncio.sleep(0.25)
        return bool(self._connected and self._ws)

    def get_allowed_contact(self, sender_id: str) -> WhatsAppContact | None:
        """Prefer the local WhatsApp contacts store when it exists."""
        if self.config.contacts_file and has_local_store(self.config.contacts_file):
            return find_contact(sender_id, load_contacts(self.config.contacts_file))
        if super().is_allowed(sender_id):
            return WhatsAppContact(phone=sender_id)
        return None

    def get_allowed_group_member(
        self,
        group_id: str,
        group_name: str,
        member_id: str,
        member_pn: str,
    ) -> tuple[int, GroupReplyTarget] | None:
        """Return the matching JSON reply-target row for a group member when one exists."""
        try:
            rows = load_group_reply_targets(self._reply_targets_file)
        except Exception:
            logger.exception("Failed to read WhatsApp group reply targets")
            return None
        return match_group_reply_target(
            rows,
            group_id=group_id,
            group_name=group_name,
            member_id=member_id,
            member_phone=member_pn,
        )

    @staticmethod
    def _extract_phone_from_sender(sender: str) -> str:
        """Extract a phone number from old-style phone JIDs when possible."""
        text = str(sender or "").strip()
        if text.endswith("@s.whatsapp.net") or text.endswith("@c.us"):
            local = text.split("@", 1)[0]
            if local and local.lstrip("+").isdigit():
                return local
        return ""

    @staticmethod
    def _bare_chat_id(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text.split("@", 1)[0]

    @staticmethod
    def _first_nonempty(*values: str) -> str:
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _first_phone(*values: str) -> str:
        for value in values:
            phone = normalize_contact_id(str(value or ""))
            if phone:
                return phone
        return ""

    @staticmethod
    def _append_search_term(terms: list[str], seen: set[str], value: str) -> None:
        term = " ".join(str(value or "").split())
        if not term:
            return
        key = term.casefold()
        if key in seen:
            return
        seen.add(key)
        terms.append(term)

    def _get_direct_reply_target(
        self,
        *,
        phone: str = "",
        chat_id: str = "",
        sender_id: str = "",
    ) -> DirectReplyTarget | None:
        try:
            return find_direct_reply_target(
                self._reply_targets_file,
                phone=phone,
                chat_id=chat_id,
                sender_id=sender_id,
            )
        except Exception:
            logger.exception("Failed to read WhatsApp direct reply targets")
            return None

    def _get_contact_label(self, phone: str) -> WhatsAppContact | None:
        if not phone or not self.config.contacts_file or not has_local_store(self.config.contacts_file):
            return None
        try:
            return find_contact(phone, load_contacts(self.config.contacts_file))
        except Exception:
            logger.exception("Failed to load WhatsApp contacts for draft target lookup")
            return None

    def _resolve_draft_target(self, chat_id: str, metadata: dict | None = None) -> dict | None:
        """Resolve an explicit direct-chat target for Playwright draft composition."""
        outbound_chat_id = str(chat_id or "").strip()
        if not outbound_chat_id or outbound_chat_id.endswith("@g.us"):
            return None

        meta = metadata or {}
        current_sender = str(meta.get("sender", "") or "").strip()
        current_phone = self._first_phone(
            str(meta.get("sender_phone", "") or ""),
            str(meta.get("pn", "") or ""),
        )

        reply_target = self._get_direct_reply_target(
            phone=current_phone,
            chat_id=current_sender or outbound_chat_id,
            sender_id=current_sender,
        )
        if reply_target is not None and not reply_target.enabled:
            reply_target = None

        contact = self._get_contact_label(self._first_phone(current_phone, reply_target.phone if reply_target else ""))
        resolved_chat_id = self._first_nonempty(
            current_sender,
            reply_target.chat_id if reply_target else "",
            reply_target.sender_id if reply_target else "",
            outbound_chat_id,
        )
        resolved_phone = self._first_phone(
            current_phone,
            reply_target.phone if reply_target else "",
            outbound_chat_id,
        )

        search_terms: list[str] = []
        seen_terms: set[str] = set()

        for candidate in (
            str(meta.get("sender_name", "") or ""),
            str(meta.get("push_name", "") or ""),
            current_phone,
            self._bare_chat_id(current_sender),
        ):
            self._append_search_term(search_terms, seen_terms, candidate)

        if reply_target is not None:
            for candidate in (
                reply_target.push_name,
                reply_target.phone,
                self._bare_chat_id(reply_target.chat_id),
                self._bare_chat_id(reply_target.sender_id),
            ):
                self._append_search_term(search_terms, seen_terms, candidate)

        if contact is not None:
            for candidate in (contact.label, normalize_contact_id(contact.phone)):
                self._append_search_term(search_terms, seen_terms, candidate)

        self._append_search_term(search_terms, seen_terms, self._bare_chat_id(outbound_chat_id))

        if not resolved_chat_id or (not resolved_phone and not search_terms):
            return None

        target: dict[str, str | list[str]] = {
            "chatId": resolved_chat_id,
            "searchTerms": search_terms,
        }
        if resolved_phone:
            target["phone"] = resolved_phone
        return target

    def _build_direct_history_target(self, row: DirectReplyTarget) -> dict | None:
        """Build a direct-chat Playwright target from the reply-target allowlist row."""
        resolved_phone = self._first_phone(row.phone)
        resolved_chat_id = self._first_nonempty(row.chat_id, row.sender_id, resolved_phone)
        if not resolved_chat_id:
            return None

        contact = self._get_contact_label(resolved_phone)
        search_terms: list[str] = []
        seen_terms: set[str] = set()
        for candidate in (
            row.phone,
            self._bare_chat_id(row.chat_id),
            self._bare_chat_id(row.sender_id),
            row.push_name,
            contact.label if contact is not None else "",
        ):
            self._append_search_term(search_terms, seen_terms, candidate)

        if not resolved_phone and not search_terms:
            return None

        payload: dict[str, str | list[str]] = {
            "chatId": resolved_chat_id,
            "searchTerms": search_terms,
        }
        if resolved_phone:
            payload["phone"] = resolved_phone
        return payload

    def _build_direct_history_targets_payload(self) -> list[dict]:
        """Return all enabled direct reply targets as Playwright targets."""
        return self._build_scoped_direct_history_targets_payload()

    def _normalize_direct_history_scope(self, phones: object = None) -> list[str]:
        """Return a normalized, deduplicated phone scope for direct-history sync."""
        if not phones or not isinstance(phones, (list, tuple, set)):
            return []
        scoped: list[str] = []
        seen: set[str] = set()
        for value in phones:
            phone = normalize_contact_id(str(value or ""))
            if not phone or phone in seen:
                continue
            seen.add(phone)
            scoped.append(phone)
        return scoped

    def _build_scoped_direct_history_targets_payload(self, phones: list[str] | None = None) -> list[dict]:
        """Return enabled direct reply targets, optionally limited to a phone scope."""
        try:
            rows = load_direct_reply_targets(self._reply_targets_file)
        except Exception:
            logger.exception("Failed to read WhatsApp direct reply targets for web scrape")
            return []

        phone_scope = set(self._normalize_direct_history_scope(phones))
        targets: list[dict] = []
        seen_chat_ids: set[str] = set()
        for row in rows:
            if not row.enabled:
                continue
            if phone_scope and row.phone not in phone_scope:
                continue
            payload = self._build_direct_history_target(row)
            if payload is None:
                continue
            chat_id = str(payload.get("chatId", "") or "").strip()
            if not chat_id or chat_id in seen_chat_ids:
                continue
            seen_chat_ids.add(chat_id)
            targets.append(payload)
        return targets

    def _direct_contact_for_history(self, phone: str, label: str = "", push_name: str = "") -> WhatsAppContact:
        normalized_phone = normalize_contact_id(phone)
        if self.config.contacts_file and has_local_store(self.config.contacts_file):
            try:
                existing = find_contact(normalized_phone, load_contacts(self.config.contacts_file))
            except Exception:
                logger.exception("Failed to load WhatsApp contacts while building history storage")
            else:
                if existing is not None:
                    return existing
        return WhatsAppContact(phone=normalized_phone, label=label or push_name, enabled=True)

    def _cache_history_messages(self, raw_messages: list[dict]) -> None:
        """Keep a bounded cache of raw direct history for allowlist replays."""
        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            if bool(raw.get("isGroup", False)):
                continue
            message_id = str(raw.get("id", "") or "").strip()
            if not message_id:
                continue
            self._history_cache[message_id] = dict(raw)
            self._history_cache.move_to_end(message_id)
        while len(self._history_cache) > 50000:
            self._history_cache.popitem(last=False)

    async def _replay_cached_history(self, phones: list[str] | None = None) -> None:
        """Re-filter cached history using the latest direct reply-target list."""
        if not self._history_cache:
            logger.info("WhatsApp cached history replay requested, but no cached direct history is available")
            return

        phone_scope = set(self._normalize_direct_history_scope(phones))
        messages = list(self._history_cache.values())
        if phone_scope:
            messages = [
                raw
                for raw in messages
                if self._first_phone(str(raw.get("pn", "") or ""), self._extract_phone_from_sender(str(raw.get("sender", "") or ""))) in phone_scope
            ]
            if not messages:
                logger.info("WhatsApp cached history replay requested, but no cached direct history matched the requested phone scope")
                return

        await self._handle_history_batch(
            {
                "source": "history_replay",
                "messages": messages,
                "isLatest": True,
            }
        )

    async def _handle_history_batch(self, data: dict) -> None:
        """Filter bridge history down to direct reply targets and publish one import batch."""
        raw_messages = data.get("messages") or []
        if isinstance(raw_messages, list) and raw_messages:
            self._cache_history_messages(raw_messages)
        source = str(data.get("source", "") or "")
        request_id = str(data.get("requestId", "") or "").strip()

        try:
            direct_targets = load_direct_reply_targets(self._reply_targets_file)
        except Exception:
            logger.exception("Failed to read WhatsApp reply targets for history import")
            return

        if not isinstance(raw_messages, list) or not raw_messages:
            return

        entries: list[dict] = []
        per_session_meta: dict[str, dict[str, str | DirectReplyTarget]] = {}

        for raw in raw_messages:
            if not isinstance(raw, dict):
                continue
            if bool(raw.get("isGroup", False)):
                continue

            sender = str(raw.get("sender", "") or "").strip()
            raw_phone = self._first_phone(str(raw.get("pn", "") or ""), self._extract_phone_from_sender(sender))
            target = match_direct_reply_target(
                direct_targets,
                phone=raw_phone,
                chat_id=sender,
                sender_id=sender,
            )
            if target is None or not target.enabled:
                continue

            canonical_phone = self._first_phone(target.phone, raw_phone)
            message_id = str(raw.get("id", "") or "").strip()
            if not canonical_phone or not sender or not message_id:
                continue

            session_key = f"{self.name}:{canonical_phone}"
            is_from_me = bool(raw.get("fromMe", False))
            push_name = str(raw.get("pushName", "") or "").strip()
            # Only treat push_name as the *client* identity when the
            # message is NOT from us.  fromMe messages carry the
            # operator's own WhatsApp display name, which must never
            # overwrite the client's name.
            client_push_name = push_name if not is_from_me else ""
            entry = {
                "session_key": session_key,
                "chat_id": sender,
                "phone": canonical_phone,
                "sender": sender,
                "sender_id": canonical_phone,
                "content": str(raw.get("content", "") or ""),
                "message_id": message_id,
                "timestamp": raw.get("timestamp"),
                "from_me": is_from_me,
                "push_name": push_name,
            }
            entries.append(entry)

            meta = per_session_meta.setdefault(
                session_key,
                {
                    "phone": canonical_phone,
                    "chat_id": sender,
                    "push_name": client_push_name,
                    "target": target,
                },
            )
            if client_push_name and not str(meta.get("push_name", "") or "").strip():
                meta["push_name"] = client_push_name

        metadata = {
            "source": source,
            "syncType": data.get("syncType"),
            "progress": data.get("progress"),
            "isLatest": data.get("isLatest"),
        }
        if request_id:
            metadata["request_id"] = request_id

        if not entries:
            if request_id:
                await self.bus.publish_history(
                    InboundHistoryBatch(
                        channel=self.name,
                        entries=[],
                        metadata=metadata,
                    )
                )
            return

        for meta in per_session_meta.values():
            phone = str(meta.get("phone", "") or "")
            chat_id = str(meta.get("chat_id", "") or "")
            push_name = str(meta.get("push_name", "") or "")
            target = meta.get("target")
            if isinstance(target, DirectReplyTarget):
                try:
                    identified_chat_id = chat_id
                    if source == "web_scrape" and "@" not in identified_chat_id:
                        identified_chat_id = ""
                    observe_direct_identification(
                        self._reply_targets_file,
                        phone=phone,
                        chat_id=identified_chat_id,
                        sender_id=identified_chat_id,
                        push_name=push_name,
                    )
                except Exception:
                    logger.exception("Failed to update reply-target JSON direct identification from history")

        await self.bus.publish_history(
            InboundHistoryBatch(
                channel=self.name,
                entries=entries,
                metadata=metadata,
            )
        )

    async def _publish_inbound(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict | None = None,
        session_key: str | None = None,
    ) -> None:
        """Publish an already-authorized inbound message to the bus."""
        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
            session_key_override=session_key,
        )
        await self.bus.publish_inbound(msg)

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url

        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)

        self._running = True
        self._ws_reconnect_failures = 0
        if self._parse_task is None or self._parse_task.done():
            self._parse_task = asyncio.create_task(self._history_parse_worker())

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    # Send auth token if configured
                    if self.config.bridge_token:
                        await ws.send(json.dumps({"type": "auth", "token": self.config.bridge_token}))
                    self._connected = True
                    self._ws_reconnect_failures = 0
                    self._set_bridge_status(False, "")
                    logger.info("Connected to WhatsApp bridge")

                    # Listen for messages
                    async for message in ws:
                        try:
                            await self._handle_bridge_message(message)
                        except Exception as e:
                            logger.error("Error handling bridge message: {}", e)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connected = False
                self._ws = None
                self._ws_reconnect_failures += 1

                if self._ws_reconnect_failures >= self._ws_max_reconnect_failures:
                    self._set_bridge_status(True, "Bridge 进程无响应，可能已崩溃。请点击重启按钮恢复连接。")
                    logger.error(
                        "Bridge unreachable after {} reconnect attempts — escalating to frontend",
                        self._ws_reconnect_failures,
                    )
                else:
                    self._set_bridge_status(False, "")
                    logger.warning("WhatsApp bridge connection error: {}", e)

                if self._running:
                    logger.info("Reconnecting in 5 seconds... (attempt {})", self._ws_reconnect_failures)
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False
        self._parse_queue_event.set()

        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._parse_task:
            self._parse_task.cancel()
            try:
                await self._parse_task
            except asyncio.CancelledError:
                pass
            self._parse_task = None

    def _build_bridge_payload(self, msg: OutboundMessage) -> dict | None:
        """Build the bridge command for an outbound message."""
        metadata = msg.metadata or {}
        if self.config.delivery_mode == "draft" and metadata.get("_progress"):
            return None

        if self.config.delivery_mode == "draft":
            if self.config.web_browser_mode == "cdp":
                logger.info(
                    "Skipping WhatsApp draft placement in CDP mode; CDP is reserved for history parsing"
                )
                return None
            command_type = "prepare_draft"
        else:
            command_type = "send"
        text = self._restore_sender_name(msg.content, metadata)
        payload = {
            "type": command_type,
            "to": msg.chat_id,
            "text": text,
        }
        if self.config.delivery_mode == "draft":
            target = self._resolve_draft_target(msg.chat_id, metadata)
            if target is not None:
                payload["target"] = target
        return payload

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = self._build_bridge_payload(msg)
            if payload is None:
                if self.config.delivery_mode == "draft" and (msg.metadata or {}).get("_progress"):
                    logger.debug("Skipping WhatsApp progress update in draft mode")
                return
            await self._ws.send(json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.error("Error sending WhatsApp message: {}", e)

    async def _handle_bridge_message(self, raw: str) -> None:
        """Handle a message from the bridge."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from bridge: {}", raw[:100])
            return

        msg_type = data.get("type")

        if msg_type == "message":
            # Incoming message from WhatsApp
            # Deprecated by whatsapp: old phone number style typically: <phone>@s.whatspp.net
            pn = str(data.get("pn", "") or "")
            # New LID sytle typically:
            sender = str(data.get("sender", "") or "")
            content = str(data.get("content", "") or "")
            message_id = str(data.get("id", "") or "")
            is_group = bool(data.get("isGroup", False))
            participant = str(data.get("participant", "") or "")
            participant_pn = str(data.get("participantPn", "") or "")
            group_id = str(data.get("groupId", "") or sender)
            group_name = str(data.get("groupName", "") or "")
            push_name = str(data.get("pushName", "") or "")
            is_self_chat = bool(data.get("isSelfChat", False))

            if message_id:
                if message_id in self._processed_message_ids:
                    return
                self._processed_message_ids[message_id] = None
                while len(self._processed_message_ids) > 1000:
                    self._processed_message_ids.popitem(last=False)

            if self.config.delivery_mode == "draft" and is_group:
                logger.info("Ignoring WhatsApp group message in draft mode: {}", sender)
                return

            # Handle voice transcription if it's a voice message
            if content == "[Voice Message]":
                logger.info(
                    "Voice message received from {}, but direct download from bridge is not yet supported.",
                    participant or sender,
                )
                content = "[Voice Message: Transcription not available for WhatsApp yet]"

            # Extract media paths (images/documents/videos downloaded by the bridge)
            media_paths = data.get("media") or []

            # Build content tags matching Telegram's pattern: [image: /path] or [file: /path]
            if media_paths:
                for p in media_paths:
                    mime, _ = mimetypes.guess_type(p)
                    media_type = "image" if mime and mime.startswith("image/") else "file"
                    media_tag = f"[{media_type}: {p}]"
                    content = f"{content}\n{media_tag}" if content else media_tag

            if is_group:
                member_id = participant
                member_pn = participant_pn or self._extract_phone_from_sender(participant)
                logger.info(
                    "Group {} ({}) member {} pn {}",
                    group_name or "<unknown>",
                    group_id,
                    member_id or "<missing>",
                    member_pn or "<missing>",
                )

                if not member_id:
                    logger.warning("Ignoring WhatsApp group message without participant id in {}", group_id)
                    return

                group_match = self.get_allowed_group_member(group_id, group_name, member_id, member_pn)
                if group_match is None:
                    logger.warning(
                        "Access denied for WhatsApp group {} member {}. Add them to group_reply_targets in whatsapp_reply_targets.json.",
                        group_id,
                        member_id,
                    )
                    return

                row_number, row = group_match
                session_identity = normalize_contact_id(member_pn) or normalize_member_id(member_id)
                session_key = f"{self.name}:{group_id}:{session_identity}" if session_identity else None
                try:
                    observe_group_identification(
                        self._reply_targets_file,
                        group_name=row.group_name or group_name,
                        member_phone=row.member_phone or member_pn,
                        group_id=row.group_id or group_id,
                        member_id=row.member_id or member_id,
                        member_label=row.member_label or push_name,
                    )
                except Exception:
                    logger.exception("Failed to update reply-target JSON group identification")
                await self._publish_inbound(
                    sender_id=member_id,
                    chat_id=group_id,
                    content=content,
                    media=media_paths,
                    metadata={
                        "message_id": message_id,
                        "timestamp": data.get("timestamp"),
                        "is_group": True,
                        "pn": member_pn,
                        "sender_phone": member_pn,
                        "sender": member_id,
                        "sender_name": push_name,
                        "group_id": group_id,
                        "group_name": group_name,
                        "push_name": push_name,
                    },
                    session_key=session_key,
                )
                return

            resolved_pn = pn or self._extract_phone_from_sender(sender)
            user_id = resolved_pn if resolved_pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id
            session_identity = normalize_contact_id(resolved_pn) or sender
            logger.info("Sender {} pn {}", sender, resolved_pn or "<missing>")

            if is_self_chat:
                contact = WhatsAppContact(phone=resolved_pn or sender_id, label=push_name or "self-chat", enabled=True)
            else:
                contact = self.get_allowed_contact(sender_id)
                if contact is None:
                    logger.warning(
                        "Access denied for sender {} on channel {}. Add them to allowFrom or the local contacts store.",
                        sender_id,
                        self.name,
                    )
                    return

            session_key = f"{self.name}:{session_identity}" if session_identity else None
            try:
                observe_direct_identification(
                    self._reply_targets_file,
                    phone=resolved_pn or sender_id,
                    chat_id=sender,
                    sender_id=sender,
                    push_name=push_name,
                )
            except Exception:
                logger.exception("Failed to update reply-target JSON direct identification")

            direct_reply_target = None
            capture_only = is_self_chat
            if self.config.delivery_mode == "draft" and not is_self_chat:
                direct_reply_target = self._get_direct_reply_target(
                    phone=resolved_pn or sender_id,
                    chat_id=sender,
                    sender_id=sender,
                )
                capture_only = direct_reply_target is None or not direct_reply_target.enabled
                if capture_only:
                    logger.info(
                        "Capturing WhatsApp direct message without auto-reply target in draft mode: {}",
                        sender,
                    )

            await self._publish_inbound(
                sender_id=sender_id,
                chat_id=sender,  # Use full chat ID for replies
                content=content,
                media=media_paths,
                metadata={
                    "message_id": message_id,
                    "timestamp": data.get("timestamp"),
                    "is_group": False,
                    "pn": resolved_pn,
                    "sender_phone": resolved_pn,
                    "sender": sender,
                    "sender_name": push_name,
                    "push_name": push_name,
                    "is_self_chat": is_self_chat,
                    "capture_only": capture_only,
                    "auto_reply_target": bool(direct_reply_target and direct_reply_target.enabled),
                    "reply_target_phone": direct_reply_target.phone if direct_reply_target else "",
                    "reply_target_chat_id": direct_reply_target.chat_id if direct_reply_target else "",
                    "reply_target_sender_id": direct_reply_target.sender_id if direct_reply_target else "",
                    "reply_target_push_name": direct_reply_target.push_name if direct_reply_target else "",
                    "reply_target_label": direct_reply_target.label if direct_reply_target else "",
                },
                session_key=session_key,
            )

        elif msg_type == "history":
            await self._handle_history_batch(data)

        elif msg_type == "status":
            # Connection status update
            status = data.get("status")
            logger.info("WhatsApp status: {}", status)

            if status == "connected":
                self._connected = True
                self._set_auth_status(False, "", "")
                self._set_bridge_status(False, "")
            elif status == "disconnected":
                self._connected = False
                self._set_bridge_status(True, "WhatsApp 已断开连接，请重新登录")

        elif msg_type == "qr":
            # QR code for authentication
            qr = str(data.get("qr", "") or "")
            self._set_auth_status(True, qr, "Scan the QR code in the UI to reconnect WhatsApp")
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")

        elif msg_type == "deleted":
            deleted_message_id = str(data.get("deletedMessageId", "") or "")
            sender = str(data.get("sender", "") or "")
            pn = str(data.get("pn", "") or "")
            is_group = bool(data.get("isGroup", False))
            participant = str(data.get("participant", "") or "")
            participant_pn = str(data.get("participantPn", "") or "")
            group_id = str(data.get("groupId", "") or sender)
            group_name = str(data.get("groupName", "") or "")
            push_name = str(data.get("pushName", "") or "")
            deleted_at = data.get("timestamp")

            if not deleted_message_id:
                logger.warning("Ignoring WhatsApp deleted event without message id for {}", sender or "<unknown>")
                return

            if is_group:
                member_id = participant
                member_pn = participant_pn or self._extract_phone_from_sender(participant)
                if not member_id:
                    logger.warning("Ignoring WhatsApp group deletion without participant id in {}", group_id)
                    return

                group_match = self.get_allowed_group_member(group_id, group_name, member_id, member_pn)
                if group_match is None:
                    logger.warning(
                        "Ignoring delete event for unmatched WhatsApp group {} member {}",
                        group_id,
                        member_id,
                    )
                    return

                _row_number, row = group_match
                session_identity = normalize_contact_id(member_pn) or normalize_member_id(member_id)
                session_key = f"{self.name}:{group_id}:{session_identity}" if session_identity else None
                await self._publish_inbound(
                    sender_id=member_id,
                    chat_id=group_id,
                    content="",
                    metadata={
                        "event_type": "message_deleted",
                        "capture_only": True,
                        "deleted_message_id": deleted_message_id,
                        "deleted_by_sender": True,
                        "deleted_at": deleted_at,
                        "is_group": True,
                        "pn": row.member_phone or member_pn,
                        "sender_phone": row.member_phone or member_pn,
                        "sender": row.member_id or member_id,
                        "sender_name": row.member_label or push_name,
                        "group_id": row.group_id or group_id,
                        "group_name": row.group_name or group_name,
                    },
                    session_key=session_key,
                )
                return

            resolved_pn = pn or self._extract_phone_from_sender(sender)
            user_id = resolved_pn if resolved_pn else sender
            sender_id = user_id.split("@")[0] if "@" in user_id else user_id
            contact = self.get_allowed_contact(sender_id)
            if contact is None:
                logger.warning("Ignoring delete event for unlisted WhatsApp sender {}", sender_id)
                return

            session_identity = normalize_contact_id(resolved_pn) or sender
            session_key = f"{self.name}:{session_identity}" if session_identity else None
            await self._publish_inbound(
                sender_id=sender_id,
                chat_id=sender,
                content="",
                metadata={
                    "event_type": "message_deleted",
                    "capture_only": True,
                    "deleted_message_id": deleted_message_id,
                    "deleted_by_sender": True,
                    "deleted_at": deleted_at,
                    "is_group": False,
                    "pn": resolved_pn,
                    "sender_phone": resolved_pn,
                    "sender": sender,
                    "sender_name": push_name,
                },
                session_key=session_key,
            )

        elif msg_type == "ack":
            action = data.get("action", "unknown")
            status = data.get("status", "unknown")
            to = data.get("to", "")
            detail = data.get("detail")
            request_id = str(data.get("requestId", "") or "").strip()
            if action in {"scrape_direct_history", "scrape_reply_targets_history"} and request_id:
                pending = self._pending_history_sync_acks.get(request_id)
                if pending is not None and not pending.done():
                    pending.set_result(data)
            if detail:
                logger.info("WhatsApp bridge {} ack for {}: {} ({})", action, to, status, detail)
            else:
                logger.info("WhatsApp bridge {} ack for {}: {}", action, to, status)

        elif msg_type == "error":
            logger.error("WhatsApp bridge error: {}", data.get('error'))

    @staticmethod
    def _restore_sender_name(text: str, metadata: dict | None = None) -> str:
        """Restore only the sender-name placeholder before outbound chat delivery."""
        if not isinstance(text, str) or "Unknown Sender Name" not in text:
            return text
        meta = metadata or {}
        sender_name = " ".join(str(meta.get("sender_name") or meta.get("push_name") or "").split())
        if not sender_name:
            return text
        return text.replace("Unknown Sender Name", sender_name)
