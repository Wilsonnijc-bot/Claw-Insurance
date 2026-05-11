"""Local WhatsApp contacts store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from nanobot.utils.paths import confine_path, project_root


@dataclass(frozen=True)
class WhatsAppContact:
    """One locally stored WhatsApp contact."""

    phone: str
    label: str = ""
    enabled: bool = True


def contacts_path(path_str: str) -> Path:
    """Return the expanded local contacts file path (project-confined)."""
    path = Path(path_str)
    if path.is_absolute():
        return confine_path(path)
    return confine_path(project_root() / path)


def init_contacts_store(path_str: str) -> Path:
    """Create the contacts file if it does not exist yet."""
    path = contacts_path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"contacts": []}, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return path


def load_contacts(path_str: str) -> list[WhatsAppContact]:
    """Load contacts from the local file. Missing file means no local store yet."""
    path = contacts_path(path_str)
    if not path.exists():
        return []

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    raw_contacts = data.get("contacts", [])
    contacts: list[WhatsAppContact] = []
    for item in raw_contacts:
        if isinstance(item, str):
            normalized = normalize_contact_id(item)
            if normalized:
                contacts.append(WhatsAppContact(phone=item))
            continue
        if not isinstance(item, dict):
            continue
        phone = str(item.get("phone", "")).strip()
        if not normalize_contact_id(phone):
            continue
        contacts.append(
            WhatsAppContact(
                phone=phone,
                label=str(item.get("label", "")).strip(),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return contacts


def save_contacts(path_str: str, contacts: list[WhatsAppContact]) -> Path:
    """Write the contacts file."""
    path = init_contacts_store(path_str)
    payload = {
        "contacts": [
            {
                "phone": contact.phone,
                "label": contact.label,
                "enabled": contact.enabled,
            }
            for contact in contacts
        ]
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def normalize_contact_id(value: str) -> str:
    """Normalize a WhatsApp phone-like identifier for matching."""
    text = str(value or "").strip()
    if "@" in text:
        text = text.split("@", 1)[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits


def has_local_store(path_str: str) -> bool:
    """Return True when a local contacts file exists."""
    return contacts_path(path_str).exists()


def is_contact_allowed(sender_id: str, contacts: list[WhatsAppContact]) -> bool:
    """Check whether the sender is present and enabled in the contacts list."""
    sender = normalize_contact_id(sender_id)
    if not sender:
        return False

    for contact in contacts:
        if contact.enabled and normalize_contact_id(contact.phone) == sender:
            return True
    return False


def find_contact(sender_id: str, contacts: list[WhatsAppContact]) -> WhatsAppContact | None:
    """Return the enabled contact entry that matches the sender."""
    sender = normalize_contact_id(sender_id)
    if not sender:
        return None

    for contact in contacts:
        if contact.enabled and normalize_contact_id(contact.phone) == sender:
            return contact
    return None
