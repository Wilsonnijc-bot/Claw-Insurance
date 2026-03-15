"""JSON storage for WhatsApp reply targets driven by self-chat commands."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.channels.whatsapp_contacts import normalize_contact_id
from nanobot.channels.whatsapp_group_members import normalize_group_name

_DEFAULT_REL_PATH = "data/whatsapp_reply_targets.json"


def reply_targets_path(path_str: str, project_root: Path) -> Path:
    """Resolve reply-target JSON path (relative paths are rooted at project root)."""
    raw = str(path_str or "").strip()
    candidate = Path(raw).expanduser() if raw else Path(_DEFAULT_REL_PATH)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate


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
