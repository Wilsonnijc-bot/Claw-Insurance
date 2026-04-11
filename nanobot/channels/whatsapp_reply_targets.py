"""JSON storage for WhatsApp reply targets driven by self-chat commands."""

from __future__ import annotations

from dataclasses import dataclass
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.channels.whatsapp_contacts import load_contacts, normalize_contact_id
from nanobot.channels.whatsapp_group_members import load_group_members, normalize_group_name

_DEFAULT_REL_PATH = "data/whatsapp_reply_targets.json"
_LEGACY_CONTACTS_REL_PATH = Path("data") / "contacts" / "whatsapp.json"


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
        save_reply_targets(path, _default_payload())
    return path


def load_reply_targets(
    path: Path,
    *,
    project_root: Path | None = None,
    group_members_file: str = "",
) -> dict[str, Any]:
    """Load reply-target store with schema defaults."""
    init_reply_targets_store(path)
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)

    payload, changed = _normalize_payload(payload)
    effective_project_root: Path | None = None
    if project_root is not None:
        try:
            path.resolve().relative_to(Path(project_root).resolve())
        except ValueError:
            effective_project_root = None
        else:
            effective_project_root = Path(project_root)
    changed = _migrate_legacy_contacts(payload, project_root=effective_project_root) or changed
    changed = _migrate_legacy_group_members(
        payload,
        group_members_file=group_members_file,
    ) or changed
    if changed:
        payload["updated_at"] = _now_iso()
        save_reply_targets(path, payload)
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
        existing_by_phone: dict[str, dict[str, Any]] = {}
        for raw in payload.get("direct_reply_targets", []):
            if not isinstance(raw, dict):
                continue
            phone = normalize_contact_id(str(raw.get("phone", "")))
            if phone and phone not in existing_by_phone:
                existing_by_phone[phone] = dict(raw)
        seen: set[str] = set()
        direct_rows: list[dict[str, Any]] = []
        for raw_phone in individuals:
            phone = normalize_contact_id(raw_phone)
            if not phone or phone in seen:
                continue
            seen.add(phone)
            direct_rows.append(_direct_row(existing_by_phone.get(phone), phone=phone, enabled=True))
        payload["direct_reply_targets"] = direct_rows
        changed = True

    if groups is not None:
        existing_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in payload.get("group_reply_targets", []):
            if not isinstance(raw, dict):
                continue
            key = (
                normalize_group_name(str(raw.get("group_name", ""))),
                normalize_contact_id(str(raw.get("member_phone", ""))),
            )
            if key[0] and key[1] and key not in existing_by_key:
                existing_by_key[key] = dict(raw)
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
                _group_row(
                    existing_by_key.get(key),
                    group_name=group_name,
                    member_phone=member_phone,
                    enabled=True,
                )
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


def upsert_direct_reply_target(
    path: Path,
    *,
    phone: str,
    label: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Upsert one direct reply target while preserving discovered metadata."""
    normalized_phone = normalize_contact_id(phone)
    if not normalized_phone:
        raise ValueError("Invalid phone number")

    payload = load_reply_targets(path)
    rows = payload.setdefault("direct_reply_targets", [])
    match_index = -1
    existing: dict[str, Any] | None = None
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        if normalize_contact_id(str(raw.get("phone", ""))) != normalized_phone:
            continue
        match_index = index
        existing = dict(raw)
        break

    row = _direct_row(existing, phone=normalized_phone, enabled=enabled, label=label)
    if match_index >= 0:
        rows[match_index] = row
    else:
        rows.append(row)
        rows.sort(key=lambda item: normalize_contact_id(str(item.get("phone", ""))))

    payload["updated_at"] = _now_iso()
    save_reply_targets(path, payload)
    return row


def remove_direct_reply_target(path: Path, *, phone: str) -> bool:
    """Remove one direct reply target by phone."""
    normalized_phone = normalize_contact_id(phone)
    if not normalized_phone:
        return False

    payload = load_reply_targets(path)
    rows = payload.get("direct_reply_targets", [])
    kept = [
        row
        for row in rows
        if not isinstance(row, dict) or normalize_contact_id(str(row.get("phone", ""))) != normalized_phone
    ]
    if len(kept) == len(rows):
        return False

    payload["direct_reply_targets"] = kept
    payload["updated_at"] = _now_iso()
    save_reply_targets(path, payload)
    return True


def upsert_group_reply_target(
    path: Path,
    *,
    group_id: str = "",
    group_name: str = "",
    member_id: str = "",
    member_phone: str = "",
    member_label: str = "",
    enabled: bool = True,
) -> dict[str, Any]:
    """Upsert one group reply target while preserving discovered metadata."""
    normalized_group_name = " ".join(str(group_name or "").split())
    normalized_group_name_key = normalize_group_name(normalized_group_name)
    normalized_member_phone = normalize_contact_id(member_phone)
    normalized_group_id = str(group_id or "").strip().lower()
    normalized_member_id = str(member_id or "").strip().lower()
    if not normalized_group_id and not normalized_group_name_key:
        raise ValueError("Provide at least one group identifier")
    if not normalized_member_id and not normalized_member_phone:
        raise ValueError("Provide at least one member identifier")

    payload = load_reply_targets(path)
    rows = payload.setdefault("group_reply_targets", [])
    match_index = -1
    existing: dict[str, Any] | None = None
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        if normalized_group_id and str(raw.get("group_id", "")).strip().lower() != normalized_group_id:
            continue
        if normalized_group_name_key and normalize_group_name(str(raw.get("group_name", ""))) != normalized_group_name_key:
            continue
        if normalized_member_id and str(raw.get("member_id", "")).strip().lower() != normalized_member_id:
            continue
        if normalized_member_phone and normalize_contact_id(str(raw.get("member_phone", ""))) != normalized_member_phone:
            continue
        match_index = index
        existing = dict(raw)
        break

    row = _group_row(
        existing,
        group_name=normalized_group_name or str(existing.get("group_name", "") if existing else ""),
        member_phone=normalized_member_phone or str(existing.get("member_phone", "") if existing else ""),
        enabled=enabled,
        group_id=group_id,
        member_id=member_id,
        member_label=member_label,
    )
    if match_index >= 0:
        rows[match_index] = row
    else:
        rows.append(row)
        rows.sort(
            key=lambda item: (
                normalize_group_name(str(item.get("group_name", ""))),
                normalize_contact_id(str(item.get("member_phone", ""))),
                str(item.get("group_id", "")).strip().lower(),
                str(item.get("member_id", "")).strip().lower(),
            )
        )

    payload["updated_at"] = _now_iso()
    save_reply_targets(path, payload)
    return row


def remove_group_reply_target(
    path: Path,
    *,
    group_id: str = "",
    group_name: str = "",
    member_id: str = "",
    member_phone: str = "",
) -> bool:
    """Remove one group reply target row."""
    normalized_group_id = str(group_id or "").strip().lower()
    normalized_group_name = normalize_group_name(group_name)
    normalized_member_id = str(member_id or "").strip().lower()
    normalized_member_phone = normalize_contact_id(member_phone)
    if not normalized_group_id and not normalized_group_name:
        return False
    if not normalized_member_id and not normalized_member_phone:
        return False

    def _keep(raw: Any) -> bool:
        if not isinstance(raw, dict):
            return True
        if normalized_group_id and str(raw.get("group_id", "")).strip().lower() != normalized_group_id:
            return True
        if normalized_group_name and normalize_group_name(str(raw.get("group_name", ""))) != normalized_group_name:
            return True
        member_matches = False
        if normalized_member_id and str(raw.get("member_id", "")).strip().lower() == normalized_member_id:
            member_matches = True
        if normalized_member_phone and normalize_contact_id(str(raw.get("member_phone", ""))) == normalized_member_phone:
            member_matches = True
        return not member_matches

    payload = load_reply_targets(path)
    rows = payload.get("group_reply_targets", [])
    kept = [row for row in rows if _keep(row)]
    if len(kept) == len(rows):
        return False

    payload["group_reply_targets"] = kept
    payload["updated_at"] = _now_iso()
    save_reply_targets(path, payload)
    return True


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


def _default_payload() -> dict[str, Any]:
    return {
        "version": 1,
        "updated_at": "",
        "source": "",
        "migrations": {
            "contacts_file_imported": False,
            "group_members_file_imported": False,
        },
        "direct_reply_targets": [],
        "group_reply_targets": [],
    }


def _normalize_payload(payload: Any) -> tuple[dict[str, Any], bool]:
    changed = False
    if not isinstance(payload, dict):
        payload = {}
        changed = True
    payload.setdefault("version", 1)
    payload.setdefault("updated_at", "")
    payload.setdefault("source", "")
    if not isinstance(payload.get("migrations"), dict):
        payload["migrations"] = {}
        changed = True
    migrations = payload["migrations"]
    if "contacts_file_imported" not in migrations:
        migrations["contacts_file_imported"] = False
        changed = True
    if "group_members_file_imported" not in migrations:
        migrations["group_members_file_imported"] = False
        changed = True
    if not isinstance(payload.get("direct_reply_targets"), list):
        payload["direct_reply_targets"] = []
        changed = True
    if not isinstance(payload.get("group_reply_targets"), list):
        payload["group_reply_targets"] = []
        changed = True
    return payload, changed


def _migrate_legacy_contacts(payload: dict[str, Any], *, project_root: Path | None) -> bool:
    migrations = payload.setdefault("migrations", {})
    if migrations.get("contacts_file_imported"):
        return False

    migrations["contacts_file_imported"] = True
    changed = True
    if project_root is None:
        return changed

    legacy_contacts_file = Path(project_root) / _LEGACY_CONTACTS_REL_PATH
    if not legacy_contacts_file.exists():
        return changed

    existing_by_phone: dict[str, dict[str, Any]] = {}
    for raw in payload.get("direct_reply_targets", []):
        if not isinstance(raw, dict):
            continue
        phone = normalize_contact_id(str(raw.get("phone", "")))
        if phone and phone not in existing_by_phone:
            existing_by_phone[phone] = raw

    for contact in load_contacts(str(legacy_contacts_file)):
        if not contact.enabled:
            continue
        phone = normalize_contact_id(contact.phone)
        if not phone:
            continue
        row = existing_by_phone.get(phone)
        if row is None:
            row = _direct_row(None, phone=phone, enabled=True, label=contact.label)
            payload.setdefault("direct_reply_targets", []).append(row)
            existing_by_phone[phone] = row
            changed = True
            continue
        if not bool(row.get("enabled", True)):
            row["enabled"] = True
            changed = True
        label = str(contact.label or "").strip()
        if label and not str(row.get("label", "")).strip():
            row["label"] = label
            changed = True

    payload["direct_reply_targets"] = sorted(
        payload.get("direct_reply_targets", []),
        key=lambda item: normalize_contact_id(str(item.get("phone", ""))),
    )
    return changed


def _migrate_legacy_group_members(payload: dict[str, Any], *, group_members_file: str) -> bool:
    if not str(group_members_file or "").strip():
        return False

    migrations = payload.setdefault("migrations", {})
    if migrations.get("group_members_file_imported"):
        return False

    migrations["group_members_file_imported"] = True
    changed = True
    if payload.get("group_reply_targets"):
        return changed

    imported_rows: list[dict[str, Any]] = []
    for row in load_group_members(group_members_file):
        if not row.enabled:
            continue
        group_name = " ".join(str(row.group_name or "").split())
        member_phone = normalize_contact_id(row.member_pn)
        if not group_name or not member_phone:
            continue
        imported_rows.append(
            _group_row(
                None,
                group_name=group_name,
                member_phone=member_phone,
                enabled=True,
                group_id=row.group_id,
                member_id=row.member_id,
                member_label=row.member_label,
            )
        )

    if imported_rows:
        payload["group_reply_targets"] = imported_rows
        changed = True
    return changed


def _direct_row(
    existing: dict[str, Any] | None,
    *,
    phone: str,
    enabled: bool,
    label: str | None = None,
) -> dict[str, Any]:
    row = dict(existing or {})
    row["phone"] = normalize_contact_id(phone)
    row["enabled"] = bool(enabled)
    if label is not None:
        row["label"] = str(label or "").strip()
    else:
        row["label"] = str(row.get("label", "") or "").strip()
    row["chat_id"] = str(row.get("chat_id", "") or "").strip()
    row["sender_id"] = str(row.get("sender_id", "") or "").strip()
    row["push_name"] = str(row.get("push_name", "") or "").strip()
    row["last_seen_at"] = str(row.get("last_seen_at", "") or "").strip()
    return row


def _group_row(
    existing: dict[str, Any] | None,
    *,
    group_name: str,
    member_phone: str,
    enabled: bool,
    group_id: str | None = None,
    member_id: str | None = None,
    member_label: str | None = None,
) -> dict[str, Any]:
    row = dict(existing or {})
    normalized_group_name = " ".join(str(group_name or "").split())
    row["group_name"] = normalized_group_name
    row["group_name_normalized"] = normalize_group_name(normalized_group_name)
    row["group_id"] = str(group_id if group_id is not None else row.get("group_id", "") or "").strip()
    row["member_phone"] = normalize_contact_id(member_phone)
    row["member_id"] = str(member_id if member_id is not None else row.get("member_id", "") or "").strip()
    row["member_label"] = str(member_label if member_label is not None else row.get("member_label", "") or "").strip()
    row["enabled"] = bool(enabled)
    row["last_seen_at"] = str(row.get("last_seen_at", "") or "").strip()
    return row
