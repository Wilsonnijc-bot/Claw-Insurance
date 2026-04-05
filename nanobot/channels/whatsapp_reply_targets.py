"""JSON storage for WhatsApp reply targets driven by self-chat commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.channels.whatsapp_contacts import normalize_contact_id
from nanobot.channels.whatsapp_group_members import normalize_group_name

_DEFAULT_REL_PATH = "data/whatsapp_reply_targets.json"


@dataclass(frozen=True)
class DirectReplyTarget:
    """One direct auto-reply target persisted from self-chat routing."""

    phone: str
    enabled: bool = True
    label: str = ""
    chat_id: str = ""
    sender_id: str = ""
    push_name: str = ""
    last_seen_at: str = ""


@dataclass(frozen=True)
class GroupReplyTarget:
    """One group auto-reply target persisted from self-chat routing."""

    group_name: str
    member_phone: str
    enabled: bool = True
    group_id: str = ""
    member_id: str = ""
    member_label: str = ""
    last_seen_at: str = ""


def reply_targets_path(path_str: str, project_root: Path) -> Path:
    """Resolve reply-target JSON path (relative paths are rooted at project root).

    ``expanduser`` is no longer called — tilde stays literal so paths
    cannot silently escape to the home directory.
    """
    from nanobot.utils.paths import confine_path

    raw = str(path_str or "").strip()
    candidate = Path(raw) if raw else Path(_DEFAULT_REL_PATH)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return confine_path(candidate)


def init_reply_targets_store(path: Path) -> Path:
    """Create reply-target store file with an empty payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        save_reply_targets(
            path,
            {
                "version": 1,
                "updated_at": "",
                "source": "",
                "direct_reply_targets": [],
                "group_reply_targets": [],
            },
        )
    return path


def load_reply_targets(path: Path) -> dict[str, Any]:
    """Load reply-target store with schema defaults."""
    init_reply_targets_store(path)
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", 1)
    payload.setdefault("updated_at", "")
    payload.setdefault("source", "")
    payload.setdefault("direct_reply_targets", [])
    payload.setdefault("group_reply_targets", [])
    return payload


def save_reply_targets(path: Path, payload: dict[str, Any]) -> Path:
    """Persist reply-target store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def load_direct_reply_targets(path: Path) -> list[DirectReplyTarget]:
    """Load valid direct reply-target rows from the JSON store."""
    payload = load_reply_targets(path)
    rows: list[DirectReplyTarget] = []
    for raw in payload.get("direct_reply_targets", []):
        if not isinstance(raw, dict):
            continue
        phone = normalize_contact_id(str(raw.get("phone", "")))
        if not phone:
            continue
        rows.append(
            DirectReplyTarget(
                phone=phone,
                enabled=bool(raw.get("enabled", True)),
                label=str(raw.get("label", "")).strip(),
                chat_id=str(raw.get("chat_id", "")).strip(),
                sender_id=str(raw.get("sender_id", "")).strip(),
                push_name=str(raw.get("push_name", "")).strip(),
                last_seen_at=str(raw.get("last_seen_at", "")).strip(),
            )
        )
    return rows


def load_group_reply_targets(path: Path) -> list[GroupReplyTarget]:
    """Load valid group reply-target rows from the JSON store."""
    payload = load_reply_targets(path)
    rows: list[GroupReplyTarget] = []
    for raw in payload.get("group_reply_targets", []):
        if not isinstance(raw, dict):
            continue
        group_name = " ".join(str(raw.get("group_name", "")).split())
        member_phone = normalize_contact_id(str(raw.get("member_phone", "")))
        if not group_name or not member_phone:
            continue
        rows.append(
            GroupReplyTarget(
                group_name=group_name,
                member_phone=member_phone,
                enabled=bool(raw.get("enabled", True)),
                group_id=str(raw.get("group_id", "")).strip(),
                member_id=str(raw.get("member_id", "")).strip(),
                member_label=str(raw.get("member_label", "")).strip(),
                last_seen_at=str(raw.get("last_seen_at", "")).strip(),
            )
        )
    return rows


def find_direct_reply_target(
    path: Path,
    *,
    phone: str = "",
    chat_id: str = "",
    sender_id: str = "",
) -> DirectReplyTarget | None:
    """Return the first direct reply target matching phone or chat identifiers."""
    rows = load_direct_reply_targets(path)
    return match_direct_reply_target(rows, phone=phone, chat_id=chat_id, sender_id=sender_id)


def match_direct_reply_target(
    rows: list[DirectReplyTarget],
    *,
    phone: str = "",
    chat_id: str = "",
    sender_id: str = "",
) -> DirectReplyTarget | None:
    """Return the first direct reply target matching phone or chat identifiers.

    Matching priority: phone (digits-only) → chat_id → sender_id.
    After a chat_id/sender_id match, the matched row's phone is cross-
    validated against the incoming phone (when available) so that stale
    identifiers cannot pull in the wrong client.
    """
    phone_norm = normalize_contact_id(phone)
    chat_id_norm = _normalize_chat_identifier(chat_id)
    sender_id_norm = _normalize_chat_identifier(sender_id)

    if phone_norm:
        for row in rows:
            if row.phone == phone_norm:
                return row

    if chat_id_norm:
        for row in rows:
            if _normalize_chat_identifier(row.chat_id) == chat_id_norm:
                # Cross-validate: if we have an incoming phone, it must
                # agree with the matched row's phone.
                if phone_norm and row.phone != phone_norm:
                    continue
                return row

    if sender_id_norm:
        for row in rows:
            if _normalize_chat_identifier(row.sender_id) == sender_id_norm:
                if phone_norm and row.phone != phone_norm:
                    continue
                return row

    return None


def match_group_reply_target(
    rows: list[GroupReplyTarget],
    *,
    group_id: str = "",
    group_name: str = "",
    member_id: str = "",
    member_phone: str = "",
) -> tuple[int, GroupReplyTarget] | None:
    """Return the first enabled group reply target matching the incoming member.

    When the target row has no ``group_id`` (i.e. matched by group name
    alone), the member **phone** must also match to prevent cross-group
    leakage from identically-named groups.
    """
    from loguru import logger

    incoming_group_id = str(group_id or "").strip().lower()
    incoming_group_name = normalize_group_name(group_name)
    incoming_member_id = str(member_id or "").strip().lower()
    incoming_member_id_bare = incoming_member_id.split("@", 1)[0] if "@" in incoming_member_id else incoming_member_id
    incoming_member_phone = normalize_contact_id(member_phone)

    for index, row in enumerate(rows):
        if not row.enabled:
            continue
        row_group_id = str(row.group_id or "").strip().lower()
        row_group_name = normalize_group_name(row.group_name)
        row_member_id = str(row.member_id or "").strip().lower()
        row_member_id_bare = row_member_id.split("@", 1)[0] if "@" in row_member_id else row_member_id
        row_member_phone = normalize_contact_id(row.member_phone)

        matched_by_group_id = False
        if row_group_id:
            if not incoming_group_id or row_group_id != incoming_group_id:
                continue
            matched_by_group_id = True
        elif not incoming_group_name or row_group_name != incoming_group_name:
            continue

        member_matches = False
        if row_member_phone and incoming_member_phone and row_member_phone == incoming_member_phone:
            member_matches = True
        if row_member_id and incoming_member_id and row_member_id in {incoming_member_id, incoming_member_id_bare}:
            member_matches = True
        if row_member_id_bare and incoming_member_id_bare and row_member_id_bare == incoming_member_id_bare:
            member_matches = True

        # When matched by group name only (no group_id), require phone-level
        # member match to prevent cross-group leakage from name collisions.
        if not matched_by_group_id and not (row_member_phone and incoming_member_phone and row_member_phone == incoming_member_phone):
            logger.warning(
                "Group reply target matched by name only ({}), "
                "but member phone mismatch — skipping to prevent cross-group leakage",
                row_group_name,
            )
            continue

        if not member_matches:
            continue

        return index, row

    return None


def rewrite_from_self_instruction(
    path: Path,
    *,
    individuals: list[str] | None,
    groups: list[tuple[str, str]] | None,
) -> dict[str, int]:
    """Rewrite target lists from latest self-chat command blocks."""
    payload = load_reply_targets(path)
    changed = False

    if individuals is not None:
        seen: set[str] = set()
        direct_rows: list[dict[str, Any]] = []
        for raw_phone in individuals:
            phone = normalize_contact_id(raw_phone)
            if not phone or phone in seen:
                continue
            seen.add(phone)
            direct_rows.append(
                {
                    "phone": phone,
                    "enabled": True,
                    "label": "",
                    "chat_id": "",
                    "sender_id": "",
                    "push_name": "",
                    "last_seen_at": "",
                }
            )
        payload["direct_reply_targets"] = direct_rows
        changed = True

    if groups is not None:
        seen_group_members: set[tuple[str, str]] = set()
        group_rows: list[dict[str, Any]] = []
        for raw_group_name, raw_phone in groups:
            group_name = " ".join(str(raw_group_name or "").split())
            group_name_norm = normalize_group_name(group_name)
            member_phone = normalize_contact_id(raw_phone)
            if not group_name_norm or not member_phone:
                continue
            key = (group_name_norm, member_phone)
            if key in seen_group_members:
                continue
            seen_group_members.add(key)
            group_rows.append(
                {
                    "group_name": group_name,
                    "group_name_normalized": group_name_norm,
                    "group_id": "",
                    "member_phone": member_phone,
                    "member_id": "",
                    "member_label": "",
                    "enabled": True,
                    "last_seen_at": "",
                }
            )
        payload["group_reply_targets"] = group_rows
        changed = True

    if changed:
        payload["source"] = "self_chat_command"
        payload["updated_at"] = _now_iso()
        save_reply_targets(path, payload)

    return {
        "direct_reply_target_count": len(payload.get("direct_reply_targets", [])),
        "group_reply_target_count": len(payload.get("group_reply_targets", [])),
    }


def observe_direct_identification(
    path: Path,
    *,
    phone: str,
    chat_id: str,
    sender_id: str,
    push_name: str = "",
) -> bool:
    """Fill direct target details when a real direct message is identified."""
    target_phone = normalize_contact_id(phone)
    if not target_phone:
        return False

    payload = load_reply_targets(path)
    changed = False
    for row in payload.get("direct_reply_targets", []):
        if normalize_contact_id(str(row.get("phone", ""))) != target_phone:
            continue
        if chat_id and row.get("chat_id") != chat_id:
            row["chat_id"] = chat_id
            changed = True
        if sender_id and row.get("sender_id") != sender_id:
            row["sender_id"] = sender_id
            changed = True
        if push_name and row.get("push_name") != push_name:
            row["push_name"] = push_name
            changed = True
        now = _now_iso()
        if row.get("last_seen_at") != now:
            row["last_seen_at"] = now
            changed = True
        break

    if changed:
        payload["updated_at"] = _now_iso()
        save_reply_targets(path, payload)
    return changed


def observe_group_identification(
    path: Path,
    *,
    group_name: str,
    member_phone: str,
    group_id: str = "",
    member_id: str = "",
    member_label: str = "",
) -> bool:
    """Fill group target details when a real group member is identified."""
    group_name_norm = normalize_group_name(group_name)
    member_phone_norm = normalize_contact_id(member_phone)
    if not group_name_norm or not member_phone_norm:
        return False

    payload = load_reply_targets(path)
    changed = False
    for row in payload.get("group_reply_targets", []):
        if normalize_group_name(str(row.get("group_name", ""))) != group_name_norm:
            continue
        if normalize_contact_id(str(row.get("member_phone", ""))) != member_phone_norm:
            continue
        if group_id and row.get("group_id") != group_id:
            row["group_id"] = group_id
            changed = True
        if member_id and row.get("member_id") != member_id:
            row["member_id"] = member_id
            changed = True
        if member_label and row.get("member_label") != member_label:
            row["member_label"] = member_label
            changed = True
        now = _now_iso()
        if row.get("last_seen_at") != now:
            row["last_seen_at"] = now
            changed = True
        break

    if changed:
        payload["updated_at"] = _now_iso()
        save_reply_targets(path, payload)
    return changed


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_chat_identifier(value: str) -> str:
    return str(value or "").strip().casefold()
