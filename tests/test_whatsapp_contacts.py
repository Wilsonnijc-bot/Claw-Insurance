from pathlib import Path

from nanobot.channels.whatsapp_contacts import (
    WhatsAppContact,
    has_local_store,
    init_contacts_store,
    is_contact_allowed,
    load_contacts,
    normalize_contact_id,
    save_contacts,
)


def test_whatsapp_contacts_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "whatsapp.json"
    init_contacts_store(str(path))
    save_contacts(
        str(path),
        [
            WhatsAppContact(phone="+85212345678", label="Alice"),
            WhatsAppContact(phone="+85287654321", label="Bob", enabled=False),
        ],
    )

    contacts = load_contacts(str(path))

    assert has_local_store(str(path)) is True
    assert contacts == [
        WhatsAppContact(phone="+85212345678", label="Alice", enabled=True),
        WhatsAppContact(phone="+85287654321", label="Bob", enabled=False),
    ]


def test_whatsapp_contacts_match_sender_ids_by_normalized_phone() -> None:
    contacts = [WhatsAppContact(phone="+85212345678", label="Alice")]

    assert normalize_contact_id("+852 1234 5678") == "85212345678"
    assert is_contact_allowed("85212345678@s.whatsapp.net", contacts) is True
    assert is_contact_allowed("+85212345678", contacts) is True
    assert is_contact_allowed("+85200000000", contacts) is False
