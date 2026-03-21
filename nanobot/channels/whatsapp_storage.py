"""Human-readable local storage helpers for WhatsApp sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path

from nanobot.channels.whatsapp_contacts import WhatsAppContact, normalize_contact_id
from nanobot.channels.whatsapp_group_members import (
    WhatsAppGroupMember,
    normalize_group_id,
    normalize_member_id,
)
from nanobot.utils.helpers import ensure_dir, safe_filename


def storage_path(path_str: str, workspace: Path) -> Path:
    """Resolve the WhatsApp storage root, defaulting to the current workspace."""
    base = Path(path_str).expanduser() if path_str else workspace / "whatsapp-storage"
    return ensure_dir(base)


def session_file_path(workspace: Path, session_key: str) -> Path:
    """Return the persisted JSONL session path for a session key."""
    safe_key = safe_filename(session_key.replace(":", "_"))
    return ensure_dir(workspace / "sessions") / f"{safe_key}.jsonl"


def sync_storage_readme(storage_dir: Path) -> None:
    """Create a short README that explains the WhatsApp storage layout."""
    readme = storage_dir / "README.md"
    if readme.exists():
        return
    readme.write_text(
        "# WhatsApp Storage\n\n"
        "- `direct/`: one folder per allowed direct-message contact\n"
        "- `groups/`: one folder per row in `whatsapp_groups.csv`\n"
        "- `meta.json`: identifiers and the linked session file\n"
        "- `direct/*/history.jsonl`: materialized `client` / `me` history export\n"
        "- `groups/*/history.jsonl`: symlink to the actual Nanobot session history when available\n",
        encoding="utf-8",
    )


def sync_direct_contact_storage(
    storage_dir: Path,
    workspace: Path,
    contact: WhatsAppContact,
    *,
    sender: str = "",
    push_name: str = "",
) -> Path:
    """Create or refresh the storage folder for one direct-message contact."""
    sync_storage_readme(storage_dir)
    phone = normalize_contact_id(contact.phone)
    label = _slug(contact.label or push_name or phone or sender, "contact")
    folder = ensure_dir(storage_dir / "direct" / f"{label}__{phone or _slug(sender, 'unknown')}")
    session_key = f"whatsapp:{phone}" if phone else None
    session_file = session_file_path(workspace, session_key) if session_key else None

    _write_meta(
        folder / "meta.json",
        {
            "type": "direct",
            "phone": contact.phone,
            "normalized_phone": phone,
            "label": contact.label,
            "push_name": push_name,
            "sender": sender,
            "enabled": contact.enabled,
            "session_key": session_key,
            "session_file": str(session_file) if session_file else "",
        },
    )
    _write_direct_history(folder / "history.jsonl", session_key or "", session_file)
    return folder


def sync_group_row_storage(
    storage_dir: Path,
    workspace: Path,
    row_number: int,
    row: WhatsAppGroupMember,
    *,
    push_name: str = "",
) -> Path:
    """Create or refresh the storage folder for one CSV allowlist row."""
    sync_storage_readme(storage_dir)
    group_name = row.group_name or row.group_id or "group"
    member_identity = normalize_contact_id(row.member_pn) or normalize_member_id(row.member_id)
    member_hint = row.member_label or row.member_pn or row.member_id or push_name or f"row-{row_number:03d}"
    folder_name = (
        f"row-{row_number:03d}__{_slug(group_name, 'group')}__{_slug(member_hint, 'member')}"
    )
    folder = ensure_dir(storage_dir / "groups" / folder_name)

    session_key = None
    if row.group_id and member_identity:
        session_key = f"whatsapp:{row.group_id}:{member_identity}"
    session_file = session_file_path(workspace, session_key) if session_key else None
    status = "active" if session_key else "bootstrap-pending"

    _write_meta(
        folder / "meta.json",
        {
            "type": "group",
            "row_number": row_number,
            "status": status,
            "group_id": row.group_id,
            "group_name": row.group_name,
            "normalized_group_id": normalize_group_id(row.group_id),
            "member_id": row.member_id,
            "member_pn": row.member_pn,
            "normalized_member_pn": normalize_contact_id(row.member_pn),
            "member_label": row.member_label,
            "push_name": push_name,
            "enabled": row.enabled,
            "session_key": session_key or "",
            "session_file": str(session_file) if session_file else "",
        },
    )
    _link_history(folder / "history.jsonl", session_file)
    return folder


def _slug(value: str, fallback: str) -> str:
    """Build a filesystem-friendly label."""
    text = safe_filename(str(value or "").strip().lower())
    text = text.replace(" ", "-").replace("_", "-")
    text = "-".join(part for part in text.split("-") if part)
    return text or fallback


def _write_meta(path: Path, payload: dict) -> None:
    """Write a metadata JSON file."""
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_visible_history_jsonl(path: Path, session_key: str, messages: list[dict]) -> None:
    """Write a human-readable history export, remapping WhatsApp roles when needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            payload = dict(msg)
            if session_key.startswith("whatsapp:"):
                role = str(payload.get("role", "") or "")
                if role in {"user", "client"}:
                    payload["role"] = "client"
                    payload.setdefault("from_me", False)
                elif role in {"assistant", "me"}:
                    payload["role"] = "me"
                    payload.setdefault("from_me", True)
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def refresh_direct_history_exports(storage_dir: Path, workspace: Path, *, phone: str) -> None:
    """Refresh all materialized direct history exports for one normalized phone."""
    normalized_phone = normalize_contact_id(phone)
    if not normalized_phone:
        return

    direct_root = storage_dir / "direct"
    if not direct_root.exists():
        return

    session_key = f"whatsapp:{normalized_phone}"
    session_file = session_file_path(workspace, session_key)
    for folder in direct_root.glob(f"*__{normalized_phone}"):
        _write_direct_history(folder / "history.jsonl", session_key, session_file)


def _link_history(link_path: Path, target: Path | None) -> None:
    """Point `history.jsonl` at the real session file when one is known."""
    if link_path.exists() or link_path.is_symlink():
        try:
            link_path.unlink()
        except OSError:
            pass

    if target is None:
        return

    try:
        os.symlink(target, link_path)
    except OSError:
        (link_path.parent / "history.path.txt").write_text(str(target) + "\n", encoding="utf-8")


def _write_direct_history(history_path: Path, session_key: str, session_file: Path | None) -> None:
    """Write a materialized direct-chat history export when the session exists."""
    fallback_path = history_path.parent / "history.path.txt"
    if fallback_path.exists():
        try:
            fallback_path.unlink()
        except OSError:
            pass

    if history_path.exists() or history_path.is_symlink():
        try:
            history_path.unlink()
        except OSError:
            pass

    if session_file is None or not session_key or not session_file.exists():
        return

    messages = _load_session_messages(session_file)
    write_visible_history_jsonl(history_path, session_key, messages)


def _load_session_messages(path: Path) -> list[dict]:
    """Load persisted session messages without the metadata line."""
    messages: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            payload = json.loads(text)
            if payload.get("_type") == "metadata":
                continue
            messages.append(payload)
    return messages
