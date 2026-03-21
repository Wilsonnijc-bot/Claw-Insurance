"""Session management for conversation history."""

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.channels.whatsapp_contacts import normalize_contact_id
from nanobot.channels.whatsapp_storage import write_visible_history_jsonl
from nanobot.utils.helpers import ensure_dir, safe_filename


def is_whatsapp_session_key(key: str) -> bool:
    """Return True when the session key belongs to WhatsApp."""
    return str(key or "").startswith("whatsapp:")


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


def legacy_chat_history_bundle_name(key: str) -> str:
    """Return the legacy chat-history bundle name derived directly from the session key."""
    return safe_filename(str(key or "").replace(":", "__"))


def canonical_chat_history_bundle_name(key: str) -> str:
    """Return the human-readable chat-history bundle name for a session key."""
    legacy_name = legacy_chat_history_bundle_name(key)
    if not is_whatsapp_session_key(key):
        return legacy_name

    parts = str(key or "").split(":")
    if len(parts) != 2:
        return legacy_name

    identity = str(parts[1] or "").strip()
    if not identity:
        return legacy_name

    # Canonicalize only when the direct-session key is clearly phone-derived.
    if "@" in identity and not (identity.endswith("@s.whatsapp.net") or identity.endswith("@c.us")):
        return legacy_name

    phone = normalize_contact_id(identity)
    if not phone:
        return legacy_name
    return safe_filename(f"whatsapp__{phone}")


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
        self.legacy_sessions_dir = Path.home() / ".nanobot" / "sessions"
        self.project_root = Path(__file__).resolve().parents[2]
        self.chat_histories_dir = ensure_dir(self.project_root / "Chathistories")
        self._cache: dict[str, Session] = {}
        self._backfill_chat_history_bundles()

    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"

    def _get_legacy_session_path(self, key: str) -> Path:
        """Legacy global session path (~/.nanobot/sessions/)."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.legacy_sessions_dir / f"{safe_key}.jsonl"

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

    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)
        if not path.exists():
            legacy_path = self._get_legacy_session_path(key)
            if legacy_path.exists():
                try:
                    shutil.move(str(legacy_path), str(path))
                    logger.info("Migrated session {} from legacy path", key)
                except Exception:
                    logger.exception("Failed to migrate session {}", key)

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

        self._write_chat_history_bundle(session)
        self._cache[session.key] = session

    def _write_chat_history_bundle(self, session: Session) -> None:
        """Write a human-readable session bundle under project ./Chathistories."""
        bundle_name = canonical_chat_history_bundle_name(session.key)
        bundle_dir = ensure_dir(self.chat_histories_dir / bundle_name)

        meta_payload = {
            "key": session.key,
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "last_consolidated": session.last_consolidated,
            "message_count": len(session.messages),
            "metadata": session.metadata,
            "source_session_file": str(self._get_session_path(session.key)),
        }
        (bundle_dir / "meta.json").write_text(
            json.dumps(meta_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        history_file = bundle_dir / "history.jsonl"
        write_visible_history_jsonl(history_file, session.key, session.messages)
        self._remove_legacy_chat_history_bundle(session.key, canonical_dir=bundle_dir)

    def _remove_legacy_chat_history_bundle(self, key: str, *, canonical_dir: Path) -> None:
        """Drop a legacy bundle directory after the canonical export is written."""
        legacy_dir = self.chat_histories_dir / legacy_chat_history_bundle_name(key)
        if legacy_dir == canonical_dir or not legacy_dir.exists():
            return
        try:
            shutil.rmtree(legacy_dir)
        except Exception:
            logger.exception("Failed to remove legacy chat history bundle for {}", key)

    def _backfill_chat_history_bundles(self) -> None:
        """Populate Chathistories for existing session files if missing."""
        for session_file in self.sessions_dir.glob("*.jsonl"):
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
                    bundle_name = canonical_chat_history_bundle_name(key)
                    bundle_dir = self.chat_histories_dir / bundle_name
                    meta_path = bundle_dir / "meta.json"
                    history_path = bundle_dir / "history.jsonl"
                    if meta_path.exists() and history_path.exists():
                        self._remove_legacy_chat_history_bundle(key, canonical_dir=bundle_dir)
                        continue

                session = self._load(key)
                if session is not None:
                    self._write_chat_history_bundle(session)
            except Exception:
                logger.exception("Failed to backfill chat history bundle from {}", session_file)

    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)

    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.

        Returns:
            List of session info dicts.
        """
        sessions = []

        for path in self.sessions_dir.glob("*.jsonl"):
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
