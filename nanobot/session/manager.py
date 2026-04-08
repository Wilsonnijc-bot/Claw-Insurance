"""Session management for conversation history.

Per-client isolation invariant:
    client-scoped operations must never access another client's
    conversation data.  Use ``get_for_client(ClientKey)`` for
    WhatsApp sessions.
"""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.channels.whatsapp_contacts import normalize_contact_id
from nanobot.session.client_key import ClientKey
from nanobot.utils.helpers import ensure_dir, readable_session_bundle_name, safe_filename


def is_whatsapp_session_key(key: str) -> bool:
    """Return True when the session key belongs to WhatsApp."""
    return str(key or "").startswith("whatsapp:")


def is_direct_whatsapp_session_key(key: str) -> bool:
    """Return True for direct-chat WhatsApp session keys."""
    text = str(key or "").strip()
    return text.startswith("whatsapp:") and text.count(":") == 1


def storage_role_for_session(key: str, role: str) -> str:
    """Map model-style roles to persisted WhatsApp roles when needed."""
    text = str(role or "")
    if not is_whatsapp_session_key(key):
        return text
    if text == "user":
        return "client"
    if text == "assistant":
        return "me"
    return text


def model_role_for_session(key: str, role: str) -> str:
    """Map persisted WhatsApp roles back to model/provider roles when needed."""
    text = str(role or "")
    if not is_whatsapp_session_key(key):
        return text
    if text == "client":
        return "user"
    if text == "me":
        return "assistant"
    return text


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": storage_role_for_session(self.key, role),
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()

    def get_history(
        self,
        max_messages: int | None = 500,
        *,
        include_consolidated: bool = False,
    ) -> list[dict[str, Any]]:
        """Return session history for LLM input, aligned to a user turn."""
        source = self.messages if include_consolidated else self.messages[self.last_consolidated:]
        sliced = list(source if max_messages is None else source[-max_messages:])

        # Drop leading non-user messages to avoid orphaned tool_result blocks
        for i, m in enumerate(sliced):
            if model_role_for_session(self.key, str(m.get("role", "") or "")) == "user":
                sliced = sliced[i:]
                break

        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {
                "role": model_role_for_session(self.key, str(m.get("role", "") or "")),
                "content": m.get("content", ""),
            }
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()

    def mark_message_deleted(
        self,
        *,
        message_id: str,
        deleted_by_sender: bool = True,
        deleted_at: str | None = None,
        deleter_id: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        """Annotate a previously stored message as deleted without removing it."""
        target = str(message_id or "").strip()
        if not target:
            return False

        matched = False
        for message in reversed(self.messages):
            if str(message.get("message_id") or "").strip() != target:
                continue
            message["deleted_by_sender"] = deleted_by_sender
            message["deleted_at"] = deleted_at or datetime.now().isoformat()
            if deleter_id:
                message["deleted_by"] = deleter_id
            matched = True
            break

        event = {
            "message_id": target,
            "deleted_by_sender": deleted_by_sender,
            "deleted_at": deleted_at or datetime.now().isoformat(),
            "matched_message": matched,
        }
        if deleter_id:
            event["deleted_by"] = deleter_id
        if chat_id:
            event["chat_id"] = chat_id

        deleted_events = list(self.metadata.get("deleted_messages") or [])
        replaced = False
        for index, existing in enumerate(deleted_events):
            if str(existing.get("message_id") or "").strip() == target:
                deleted_events[index] = event
                replaced = True
                break
        if not replaced:
            deleted_events.append(event)
        self.metadata["deleted_messages"] = deleted_events
        self.updated_at = datetime.now()
        return matched


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.sessions_dir = ensure_dir(self.workspace / "sessions")
        self.legacy_readable_sessions_root = self.sessions_dir / "readable"
        self.legacy_sessions_dir = self.workspace / ".nanobot-legacy" / "sessions"
        from nanobot.utils.paths import project_root
        self.project_root = project_root()
        self.reply_targets_file = self.project_root / "data" / "whatsapp_reply_targets.json"
        self._cache: dict[str, Session] = {}
        self._migrate_existing_session_layout()
        self._backfill_session_metadata_hints()

    def _legacy_flat_session_path(self, key: str) -> Path:
        """Return the pre-bundle flat JSONL path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_session_bundle_dir(self, key: str) -> Path:
        """Return the canonical per-chat bundle directory."""
        return ensure_dir(self.sessions_dir / readable_session_bundle_name(key))

    def _get_session_path(self, key: str) -> Path:
        """Get the canonical JSONL file path for a session."""
        return self._get_session_bundle_dir(key) / "session.jsonl"

    def _get_session_meta_path(self, key: str) -> Path:
        """Get the metadata JSON path for a session bundle."""
        return self._get_session_bundle_dir(key) / "meta.json"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy migrated session path inside the project."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

    def get_session_path(self, key: str) -> Path:
        """Public accessor for the canonical append-only session JSONL file."""
        return self._get_session_path(key)

    def get_session_meta_path(self, key: str) -> Path:
        """Public accessor for the canonical bundle metadata JSON file."""
        return self._get_session_meta_path(key)

    def get_readable_session_dir(self, key: str) -> Path:
        """Readable session bundle directory under workspace/sessions/."""
        return self._get_session_bundle_dir(key)

    def _iter_bundle_dirs(self) -> list[Path]:
        """Return all canonical bundle directories under sessions/."""
        bundles: list[Path] = []
        for path in self.sessions_dir.iterdir():
            if not path.is_dir():
                continue
            if path.name == "readable":
                continue
            bundles.append(path)
        return bundles

    @staticmethod
    def _read_session_key_from_jsonl(path: Path) -> str:
        """Read the session key from a JSONL file's metadata line when present."""
        try:
            with open(path, encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line:
                data = json.loads(first_line)
                key = str(data.get("key") or "").strip()
                if key:
                    return key
        except Exception:
            logger.exception("Failed to inspect session metadata from {}", path)
        return path.stem.replace("_", ":", 1)

    def _move_flat_session_file_into_bundle(self, source_path: Path) -> None:
        """Move an old flat session JSONL into the canonical bundle layout."""
        if not source_path.exists() or not source_path.is_file():
            return

        key = self._read_session_key_from_jsonl(source_path)
        if not key:
            return

        target_path = self._get_session_path(key)
        if source_path == target_path:
            return

        ensure_dir(target_path.parent)
        if target_path.exists():
            try:
                source_path.unlink()
            except OSError:
                pass
            return

        shutil.move(str(source_path), str(target_path))

    def _cleanup_legacy_readable_bundle(self, key: str) -> None:
        """Remove deprecated sessions/readable artifacts for one session."""
        legacy_dir = self.legacy_readable_sessions_root / readable_session_bundle_name(key)
        if not legacy_dir.exists():
            return

        for child in list(legacy_dir.iterdir()):
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                continue
            try:
                child.unlink()
            except OSError:
                pass

        try:
            legacy_dir.rmdir()
        except OSError:
            pass

    def _remove_legacy_readable_root_if_empty(self) -> None:
        """Delete sessions/readable once all legacy bundles are gone."""
        if not self.legacy_readable_sessions_root.exists():
            return
        try:
            next(self.legacy_readable_sessions_root.iterdir())
        except StopIteration:
            try:
                self.legacy_readable_sessions_root.rmdir()
            except OSError:
                pass

    def _prune_noncanonical_bundle_dirs(self) -> None:
        """Delete stale bundle directories that have no canonical session.jsonl."""
        for bundle_dir in self._iter_bundle_dirs():
            if (bundle_dir / "session.jsonl").exists():
                continue
            shutil.rmtree(bundle_dir, ignore_errors=True)

    def _migrate_existing_session_layout(self) -> None:
        """Upgrade old flat/readable session layouts into per-chat bundles."""
        for legacy_file in list(self.sessions_dir.glob("*.jsonl")):
            self._move_flat_session_file_into_bundle(legacy_file)

        if self.legacy_sessions_dir.exists():
            for legacy_file in list(self.legacy_sessions_dir.glob("*.jsonl")):
                self._move_flat_session_file_into_bundle(legacy_file)

        if self.legacy_readable_sessions_root.exists():
            for legacy_dir in list(self.legacy_readable_sessions_root.iterdir()):
                if not legacy_dir.is_dir():
                    continue
                target_dir = ensure_dir(self.sessions_dir / legacy_dir.name)
                legacy_meta = legacy_dir / "meta.json"
                target_meta = target_dir / "meta.json"
                if legacy_meta.exists() and not target_meta.exists():
                    try:
                        shutil.move(str(legacy_meta), str(target_meta))
                    except Exception:
                        logger.exception("Failed to migrate legacy bundle metadata from {}", legacy_meta)
                for child in list(legacy_dir.iterdir()):
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                        continue
                    try:
                        child.unlink()
                    except OSError:
                        pass
                try:
                    legacy_dir.rmdir()
                except OSError:
                    pass

        self._remove_legacy_readable_root_if_empty()
        self._prune_noncanonical_bundle_dirs()

    def _migrate_legacy_session_for_key(self, key: str) -> None:
        """Migrate any leftover legacy artifacts for a specific session key."""
        self._move_flat_session_file_into_bundle(self._legacy_flat_session_path(key))
        self._move_flat_session_file_into_bundle(self._get_legacy_session_path(key))
        self._cleanup_legacy_readable_bundle(key)
        self._remove_legacy_readable_root_if_empty()
        self._prune_noncanonical_bundle_dirs()

    @staticmethod
    def _first_nonempty(*values: Any) -> str:
        """Return the first non-empty string value."""
        for value in values:
            text = " ".join(str(value or "").split()).strip()
            if text:
                return text
        return ""

    def _reply_target_name_hints(self, normalized_phone: str) -> dict[str, str]:
        """Look up label/push_name hints from the reply-target registry."""
        if not normalized_phone or not self.reply_targets_file.exists():
            return {}
        try:
            payload = json.loads(self.reply_targets_file.read_text(encoding="utf-8"))
            for row in payload.get("direct_reply_targets", []) or []:
                if normalize_contact_id(str(row.get("phone") or "")) != normalized_phone:
                    continue
                return {
                    "client_label": str(row.get("label") or "").strip(),
                    "client_push_name": str(row.get("push_name") or "").strip(),
                    "client_chat_id": str(row.get("chat_id") or "").strip(),
                    "client_sender_id": str(row.get("sender_id") or "").strip(),
                }
        except Exception:
            logger.exception("Failed to read reply target hints from {}", self.reply_targets_file)
        return {}

    def _refresh_session_metadata_hints(self, session: Session) -> None:
        """Store directly useful client identity hints inside the session metadata."""
        if not isinstance(session.metadata, dict):
            session.metadata = {}
        meta = session.metadata

        if not is_whatsapp_session_key(session.key):
            return

        identity = str(session.key.split(":", 1)[1] if ":" in session.key else "").strip()
        normalized_phone = normalize_contact_id(identity)
        target_hints = self._reply_target_name_hints(normalized_phone)
        if identity:
            meta["client_identity"] = identity
        if normalized_phone:
            meta["client_phone"] = normalized_phone

        latest_client = next(
            (msg for msg in reversed(session.messages) if str(msg.get("role") or "") == "client"),
            None,
        )
        latest_any = next((msg for msg in reversed(session.messages) if isinstance(msg, dict)), None)

        client_label = self._first_nonempty(
            meta.get("client_label"),
            target_hints.get("client_label"),
            latest_client.get("reply_target_label") if latest_client else "",
            latest_any.get("reply_target_label") if latest_any else "",
        )
        client_push_name = self._first_nonempty(
            meta.get("client_push_name"),
            target_hints.get("client_push_name"),
            latest_client.get("push_name") if latest_client else "",
            latest_client.get("sender_name") if latest_client else "",
            latest_client.get("reply_target_push_name") if latest_client else "",
            # Do NOT fall back to latest_any push_name / sender_name here.
            # latest_any may be a fromMe message whose push_name is the
            # *operator's* WhatsApp display name, not the client's.
            latest_any.get("reply_target_push_name") if latest_any else "",
        )
        client_chat_id = self._first_nonempty(
            meta.get("client_chat_id"),
            target_hints.get("client_chat_id"),
            latest_client.get("chat_id") if latest_client else "",
            latest_any.get("chat_id") if latest_any else "",
        )
        client_sender_id = self._first_nonempty(
            meta.get("client_sender_id"),
            target_hints.get("client_sender_id"),
            latest_client.get("sender_id") if latest_client else "",
            latest_any.get("sender_id") if latest_any else "",
        )
        client_display_name = self._first_nonempty(
            meta.get("client_display_name"),
            client_label,
            client_push_name,
            normalized_phone,
            identity,
        )
        client_name = self._first_nonempty(
            client_label,
            meta.get("client_name"),
            client_push_name,
        )

        if client_label:
            meta["client_label"] = client_label
        if client_push_name:
            meta["client_push_name"] = client_push_name
        if client_name:
            meta["client_name"] = client_name
        if client_chat_id:
            meta["client_chat_id"] = client_chat_id
        if client_sender_id:
            meta["client_sender_id"] = client_sender_id
        if client_display_name:
            meta["client_display_name"] = client_display_name

    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.

        Args:
            key: Session key (usually channel:chat_id).

        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]

        session = self._load(key)
        if session is None:
            session = Session(key=key)

        self._cache[key] = session
        return session

    def get_for_client(self, client_key: ClientKey) -> Session:
        """Return the session for a specific WhatsApp client.

        Derives the session key from the :class:`ClientKey` so that
        callers never need to construct raw session key strings themselves.
        """
        return self.get_or_create(client_key.session_key)

    def read_persisted(self, key: str) -> Session:
        """Read the canonical on-disk session without consulting the cache."""
        session = self._load(key)
        if session is not None:
            return session
        return Session(key=key)

    def read_persisted_for_client(self, client_key: ClientKey) -> Session:
        """Read the canonical on-disk session for a WhatsApp client."""
        return self.read_persisted(client_key.session_key)

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            self._migrate_legacy_session_for_key(key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        if "role" in data:
                            data["role"] = storage_role_for_session(key, str(data.get("role", "") or ""))
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning("Failed to load session {}: {}", key, e)
            return None

    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)
        self._refresh_session_metadata_hints(session)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        self._write_readable_session_bundle(session)
        self._cache[session.key] = session

    @staticmethod
    def _visible_message_payload(message: dict[str, Any]) -> dict[str, str] | None:
        """Return a UI-visible message payload for persisted-history notifications."""
        role = str(message.get("role", "") or "")
        if role in {"tool", "system"}:
            return None
        if message.get("deleted_by_sender"):
            return None

        content = message.get("content", "")
        if isinstance(content, list):
            content_text = " ".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ).strip()
        else:
            content_text = str(content or "").strip()
        if not content_text:
            return None

        sender = "client" if role == "client" else ("ai" if message.get("is_ai_draft") else "agent")
        return {
            "chat_id": str(message.get("chat_id") or ""),
            "content": content_text,
            "sender": sender,
            "timestamp": str(message.get("timestamp") or ""),
        }

    def _build_persisted_history_event(
        self,
        session: Session,
        *,
        change_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> "PersistedHistoryEvent | None":
        """Build a frontend-facing history update after JSONL persistence."""
        if not is_direct_whatsapp_session_key(session.key):
            return None

        try:
            client = ClientKey.from_session_key(session.key)
        except ValueError:
            return None

        visible = None
        for message in reversed(session.messages):
            visible = self._visible_message_payload(message)
            if visible is not None:
                break

        from nanobot.bus.events import PersistedHistoryEvent

        return PersistedHistoryEvent(
            channel="whatsapp",
            session_key=session.key,
            phone=client.phone,
            change_type=change_type,
            chat_id="" if visible is None else visible["chat_id"],
            content="" if visible is None else visible["content"],
            sender="client" if visible is None else visible["sender"],
            timestamp=session.updated_at.isoformat() if visible is None or not visible["timestamp"] else visible["timestamp"],
            metadata=dict(metadata or {}),
        )

    def save_history(
        self,
        session: Session,
        *,
        bus: Any | None = None,
        change_type: str = "updated",
        metadata: dict[str, Any] | None = None,
        notify_observers: bool = False,
    ) -> None:
        """Save a session and optionally publish one persisted-history update."""
        self.save(session)
        if not notify_observers or bus is None:
            return

        event = self._build_persisted_history_event(
            session,
            change_type=change_type,
            metadata=metadata,
        )
        if event is None:
            return

        publish = getattr(bus, "publish_persisted_history", None)
        if callable(publish):
            publish(event)

    def _write_readable_session_bundle(self, session: Session) -> None:
        """Write the canonical session bundle metadata without duplicating history."""
        bundle_dir = ensure_dir(self.get_readable_session_dir(session.key))
        session_file = self._get_session_path(session.key)
        meta_file = self._get_session_meta_path(session.key)

        for duplicate_name in ("history.jsonl", "session.jsonl", "history.path.txt"):
            duplicate_path = bundle_dir / duplicate_name
            if duplicate_name == "session.jsonl":
                continue
            if duplicate_path.exists() or duplicate_path.is_symlink():
                try:
                    duplicate_path.unlink()
                except OSError:
                    pass

        meta_payload = {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "last_consolidated": session.last_consolidated,
            "message_count": len(session.messages),
            "metadata": session.metadata,
            "client_name": str(session.metadata.get("client_name") or ""),
            "display_name": str(session.metadata.get("client_display_name") or ""),
            "client_label": str(session.metadata.get("client_label") or ""),
            "client_push_name": str(session.metadata.get("client_push_name") or ""),
            "client_phone": str(session.metadata.get("client_phone") or ""),
            "client_chat_id": str(session.metadata.get("client_chat_id") or ""),
            "client_sender_id": str(session.metadata.get("client_sender_id") or ""),
            "client": {
                "name": str(session.metadata.get("client_name") or ""),
                "display_name": str(session.metadata.get("client_display_name") or ""),
                "label": str(session.metadata.get("client_label") or ""),
                "push_name": str(session.metadata.get("client_push_name") or ""),
                "phone": str(session.metadata.get("client_phone") or ""),
                "chat_id": str(session.metadata.get("client_chat_id") or ""),
                "sender_id": str(session.metadata.get("client_sender_id") or ""),
            },
            "canonical_session_file": str(session_file),
            "history_file": str(session_file),
            "session_file": str(session_file),
        }
        meta_file.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._cleanup_legacy_readable_bundle(session.key)
        self._remove_legacy_readable_root_if_empty()

    def _backfill_session_metadata_hints(self) -> None:
        """Upgrade existing session metadata with readable client identity hints."""
        for bundle_dir in self._iter_bundle_dirs():
            session_file = bundle_dir / "session.jsonl"
            if not session_file.exists():
                continue
            try:
                with open(session_file, encoding="utf-8") as f:
                    first = f.readline().strip()
                    if not first:
                        continue
                    first_data = json.loads(first)
                    if first_data.get("_type") != "metadata":
                        continue
                    key = str(first_data.get("key", "")).strip()
                    if not key:
                        continue

                session = self._load(key)
                if session is None:
                    continue
                before = json.dumps(session.metadata, ensure_ascii=False, sort_keys=True)
                self._refresh_session_metadata_hints(session)
                after = json.dumps(session.metadata, ensure_ascii=False, sort_keys=True)
                if before != after:
                    self.save(session)
                else:
                    self._write_readable_session_bundle(session)
            except Exception:
                logger.exception("Failed to backfill session metadata hints from {}", session_file)

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def delete_session(self, key: str) -> bool:
        """Delete a session bundle from disk and evict it from cache."""
        bundle_dir = self.sessions_dir / readable_session_bundle_name(key)
        deleted = False

        session_file = bundle_dir / "session.jsonl"
        meta_file = bundle_dir / "meta.json"

        for path in (session_file, meta_file):
            if not path.exists() and not path.is_symlink():
                continue
            try:
                path.unlink()
                deleted = True
            except OSError:
                logger.exception("Failed to remove session artifact {}", path)

        if bundle_dir.exists():
            for child in list(bundle_dir.iterdir()):
                try:
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink()
                    deleted = True
                except OSError:
                    logger.exception("Failed to remove bundle child {}", child)
            try:
                bundle_dir.rmdir()
            except OSError:
                logger.exception("Failed to remove session bundle dir {}", bundle_dir)

        self.invalidate(key)
        self._cleanup_legacy_readable_bundle(key)
        self._remove_legacy_readable_root_if_empty()
        return deleted

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for bundle_dir in self._iter_bundle_dirs():
            path = bundle_dir / "session.jsonl"
            if not path.exists():
                continue
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            key = data.get("key") or path.stem.replace("_", ":", 1)
                            sessions.append({
                                "key": key,
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue

        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
