"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
import mimetypes
from collections import OrderedDict
from pathlib import Path

from loguru import logger

from nanobot.bus.events import InboundHistoryBatch, InboundMessage, OutboundMessage
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
from nanobot.channels.whatsapp_storage import (
    storage_path,
    sync_direct_contact_storage,
    sync_group_row_storage,
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
        self._workspace = Path(workspace).expanduser() if workspace else Path.home() / ".nanobot" / "workspace"
        self._storage_dir = storage_path(self.config.storage_dir, self._workspace)
        self._project_root = Path(__file__).resolve().parents[2]
        self._reply_targets_file = reply_targets_path(self.config.reply_targets_file, self._project_root)
        init_reply_targets_store(self._reply_targets_file)
        self._ws = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()
        self._history_cache: OrderedDict[str, dict] = OrderedDict()

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
                reply_target.label,
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
            row.push_name,
            row.label,
            row.phone,
            self._bare_chat_id(row.chat_id),
            self._bare_chat_id(row.sender_id),
            contact.label if contact is not None else "",
            normalize_contact_id(contact.phone) if contact is not None else "",
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
            push_name = str(raw.get("pushName", "") or "").strip()
            entry = {
                "session_key": session_key,
                "chat_id": sender,
                "phone": canonical_phone,
                "sender": sender,
                "sender_id": canonical_phone,
                "content": str(raw.get("content", "") or ""),
                "message_id": message_id,
                "timestamp": raw.get("timestamp"),
                "from_me": bool(raw.get("fromMe", False)),
                "push_name": push_name,
            }
            entries.append(entry)

            meta = per_session_meta.setdefault(
                session_key,
                {
                    "phone": canonical_phone,
                    "chat_id": sender,
                    "push_name": push_name,
                    "target": target,
                },
            )
            if push_name and not str(meta.get("push_name", "") or "").strip():
                meta["push_name"] = push_name

        if not entries:
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

                try:
                    contact = self._direct_contact_for_history(phone, label=target.label, push_name=push_name)
                    sync_direct_contact_storage(
                        self._storage_dir,
                        self._workspace,
                        contact,
                        sender=chat_id,
                        push_name=push_name or target.push_name,
                    )
                except Exception:
                    logger.exception("Failed to update WhatsApp direct storage from history for {}", phone)

        await self.bus.publish_history(
            InboundHistoryBatch(
                channel=self.name,
                entries=entries,
                metadata={
                    "source": source,
                    "syncType": data.get("syncType"),
                    "progress": data.get("progress"),
                    "isLatest": data.get("isLatest"),
                },
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

    def _sync_storage_index(self) -> None:
        """Create clear local folders from the JSON reply-target store."""
        try:
            for row in load_direct_reply_targets(self._reply_targets_file):
                if not row.enabled:
                    continue
                contact = self._direct_contact_for_history(row.phone, label=row.label, push_name=row.push_name)
                sync_direct_contact_storage(
                    self._storage_dir,
                    self._workspace,
                    contact,
                    sender=row.chat_id or row.sender_id,
                    push_name=row.push_name,
                )

            for row_number, row in enumerate(load_group_reply_targets(self._reply_targets_file), start=1):
                if not row.enabled:
                    continue
                sync_group_row_storage(
                    self._storage_dir,
                    self._workspace,
                    row_number,
                    WhatsAppGroupMember(
                        group_id=row.group_id,
                        group_name=row.group_name,
                        member_id=row.member_id,
                        member_pn=row.member_phone,
                        member_label=row.member_label,
                        enabled=row.enabled,
                    ),
                )
        except Exception:
            logger.exception("Failed to sync WhatsApp storage index")

    async def start(self) -> None:
        """Start the WhatsApp channel by connecting to the bridge."""
        import websockets

        bridge_url = self.config.bridge_url
        self._sync_storage_index()

        logger.info("Connecting to WhatsApp bridge at {}...", bridge_url)

        self._running = True

        while self._running:
            try:
                async with websockets.connect(bridge_url) as ws:
                    self._ws = ws
                    # Send auth token if configured
                    if self.config.bridge_token:
                        await ws.send(json.dumps({"type": "auth", "token": self.config.bridge_token}))
                    self._connected = True
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
                logger.warning("WhatsApp bridge connection error: {}", e)

                if self._running:
                    logger.info("Reconnecting in 5 seconds...")
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the WhatsApp channel."""
        self._running = False
        self._connected = False

        if self._ws:
            await self._ws.close()
            self._ws = None

    def _build_bridge_payload(self, msg: OutboundMessage) -> dict | None:
        """Build the bridge command for an outbound message."""
        metadata = msg.metadata or {}
        if self.config.delivery_mode == "draft" and metadata.get("_progress"):
            return None

        command_type = "prepare_draft" if self.config.delivery_mode == "draft" else "send"
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
        internal_command = str(msg.metadata.get("_internal_command", "") or "")
        if internal_command in {"replay_cached_history", "sync_direct_history"}:
            scope_phones = self._normalize_direct_history_scope(msg.metadata.get("_target_phones"))
            await self._replay_cached_history(scope_phones)
            if internal_command == "replay_cached_history":
                return

            targets = self._build_scoped_direct_history_targets_payload(scope_phones)
            if not targets:
                logger.info("WhatsApp direct history scrape requested, but no enabled direct reply targets are configured")
                return
            if not self._ws or not self._connected:
                logger.warning("WhatsApp bridge not connected; skipping direct history scrape request")
                return
            try:
                await self._ws.send(json.dumps({"type": "scrape_direct_history", "targets": targets}, ensure_ascii=False))
            except Exception as e:
                logger.error("Error requesting WhatsApp direct history scrape: {}", e)
            return

        if not self._ws or not self._connected:
            logger.warning("WhatsApp bridge not connected")
            return

        try:
            payload = self._build_bridge_payload(msg)
            if payload is None:
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
                    sync_group_row_storage(
                        self._storage_dir,
                        self._workspace,
                        row_number + 1,
                        WhatsAppGroupMember(
                            group_id=row.group_id or group_id,
                            group_name=row.group_name or group_name,
                            member_id=row.member_id or member_id,
                            member_pn=row.member_phone or member_pn,
                            member_label=row.member_label,
                            enabled=row.enabled,
                        ),
                        push_name=push_name,
                    )
                except Exception:
                    logger.exception("Failed to update WhatsApp group storage for row {}", row_number + 1)
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
                sync_direct_contact_storage(
                    self._storage_dir,
                    self._workspace,
                    contact,
                    sender=sender,
                    push_name=push_name,
                )
            except Exception:
                logger.exception("Failed to update WhatsApp direct storage for {}", sender_id)
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
                await self.bus.publish_outbound(
                    OutboundMessage(
                        channel="whatsapp",
                        chat_id="",
                        content="",
                        metadata={"_internal_command": "sync_direct_history"},
                    )
                )
            elif status == "disconnected":
                self._connected = False

        elif msg_type == "qr":
            # QR code for authentication
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
