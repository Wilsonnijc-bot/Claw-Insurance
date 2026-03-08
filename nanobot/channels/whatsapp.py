"""WhatsApp channel implementation using Node.js bridge."""

import asyncio
import json
import mimetypes
from collections import OrderedDict
from pathlib import Path

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
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
    has_group_members_store,
    learn_group_member_identity,
    load_group_members,
    match_group_member,
    normalize_member_id,
)
from nanobot.channels.whatsapp_storage import (
    storage_path,
    sync_direct_contact_storage,
    sync_group_row_storage,
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
        self._ws = None
        self._connected = False
        self._processed_message_ids: OrderedDict[str, None] = OrderedDict()

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
    ) -> tuple[int, WhatsAppGroupMember] | None:
        """Return the matching local CSV row for a group member when one exists."""
        if self.config.group_members_file and has_group_members_store(self.config.group_members_file):
            rows = load_group_members(self.config.group_members_file)
            matched = match_group_member(group_id, group_name, member_id, member_pn, rows)
            if matched is None:
                return None
            match_index, match_row = matched
            if learn_group_member_identity(self.config.group_members_file, group_id, group_name, member_id, member_pn):
                logger.info(
                    "Learned WhatsApp group identity for {} / {} from bootstrap row",
                    group_name or group_id,
                    member_pn or member_id,
                )
                rows = load_group_members(self.config.group_members_file)
                matched = match_group_member(group_id, group_name, member_id, member_pn, rows)
                if matched is not None:
                    match_index, match_row = matched
            return match_index, match_row
        return None

    @staticmethod
    def _extract_phone_from_sender(sender: str) -> str:
        """Extract a phone number from old-style phone JIDs when possible."""
        text = str(sender or "").strip()
        if text.endswith("@s.whatsapp.net") or text.endswith("@c.us"):
            local = text.split("@", 1)[0]
            if local and local.lstrip("+").isdigit():
                return local
        return ""

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
        """Create clear local folders for direct contacts and group-member allowlist rows."""
        try:
            if self.config.contacts_file and has_local_store(self.config.contacts_file):
                for contact in load_contacts(self.config.contacts_file):
                    if contact.enabled:
                        sync_direct_contact_storage(self._storage_dir, self._workspace, contact)
            if self.config.group_members_file and has_group_members_store(self.config.group_members_file):
                for row_number, row in enumerate(load_group_members(self.config.group_members_file), start=1):
                    if row.enabled:
                        sync_group_row_storage(self._storage_dir, self._workspace, row_number, row)
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
        return {
            "type": command_type,
            "to": msg.chat_id,
            "text": msg.content,
        }

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through WhatsApp."""
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
                        "Access denied for WhatsApp group {} member {}. Add them to the group CSV allowlist.",
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
                            member_pn=row.member_pn or member_pn,
                            member_label=row.member_label,
                            enabled=row.enabled,
                        ),
                        push_name=push_name,
                    )
                except Exception:
                    logger.exception("Failed to update WhatsApp group storage for row {}", row_number + 1)
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
                        "sender": member_id,
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
                    "sender": sender,
                    "push_name": push_name,
                },
                session_key=session_key,
            )

        elif msg_type == "status":
            # Connection status update
            status = data.get("status")
            logger.info("WhatsApp status: {}", status)

            if status == "connected":
                self._connected = True
            elif status == "disconnected":
                self._connected = False

        elif msg_type == "qr":
            # QR code for authentication
            logger.info("Scan QR code in the bridge terminal to connect WhatsApp")

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
