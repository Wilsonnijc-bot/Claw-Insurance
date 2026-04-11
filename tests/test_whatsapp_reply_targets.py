from __future__ import annotations

from pathlib import Path

from nanobot.channels.whatsapp_contacts import WhatsAppContact, save_contacts
from nanobot.channels.whatsapp_group_members import WhatsAppGroupMember, save_group_members
from nanobot.channels.whatsapp_reply_targets import (
    find_direct_reply_target,
    load_group_reply_targets,
    load_reply_targets,
    match_group_reply_target,
    observe_direct_identification,
    observe_group_identification,
    rewrite_from_self_instruction,
    save_reply_targets,
)


def test_rewrite_from_self_instruction_writes_json_targets(tmp_path: Path) -> None:
    path = tmp_path / "data" / "whatsapp_reply_targets.json"
    stats = rewrite_from_self_instruction(
        path,
        individuals=["+85212345678", "+852 1234 5678", "+85299990000"],
        groups=[("Insurance sales", "+86 158 8725 0320"), ("Insurance sales", "+852 6943 2591")],
    )
    payload = load_reply_targets(path)

    assert stats["direct_reply_target_count"] == 2
    assert stats["group_reply_target_count"] == 2
    assert payload["source"] == "self_chat_command"
    assert payload["direct_reply_targets"][0]["phone"] == "85212345678"
    assert payload["group_reply_targets"][0]["group_name"] == "Insurance sales"
    assert payload["group_reply_targets"][0]["group_id"] == ""


def test_observe_identification_fills_blank_ids(tmp_path: Path) -> None:
    path = tmp_path / "data" / "whatsapp_reply_targets.json"
    rewrite_from_self_instruction(
        path,
        individuals=["+85212345678"],
        groups=[("Insurance sales", "+852 6943 2591")],
    )

    assert observe_direct_identification(
        path,
        phone="+85212345678",
        chat_id="85212345678@s.whatsapp.net",
        sender_id="85212345678@s.whatsapp.net",
        push_name="Alice",
    )
    assert observe_group_identification(
        path,
        group_name="Insurance sales",
        member_phone="+85269432591",
        group_id="1203630group@g.us",
        member_id="alice@lid",
        member_label="Alice",
    )

    payload = load_reply_targets(path)
    direct = payload["direct_reply_targets"][0]
    group = payload["group_reply_targets"][0]
    assert direct["chat_id"] == "85212345678@s.whatsapp.net"
    assert direct["sender_id"] == "85212345678@s.whatsapp.net"
    assert direct["push_name"] == "Alice"
    assert group["group_id"] == "1203630group@g.us"
    assert group["member_id"] == "alice@lid"
    assert group["member_label"] == "Alice"


def test_find_direct_reply_target_matches_phone_and_chat_identifiers(tmp_path: Path) -> None:
    path = tmp_path / "data" / "whatsapp_reply_targets.json"
    rewrite_from_self_instruction(path, individuals=["+85212345678"], groups=None)
    observe_direct_identification(
        path,
        phone="+85212345678",
        chat_id="85212345678@s.whatsapp.net",
        sender_id="85212345678@s.whatsapp.net",
        push_name="Alice",
    )

    by_phone = find_direct_reply_target(path, phone="+852 1234 5678")
    by_chat = find_direct_reply_target(path, chat_id="85212345678@s.whatsapp.net")

    assert by_phone is not None
    assert by_chat is not None
    assert by_phone.phone == "85212345678"
    assert by_chat.push_name == "Alice"


def test_match_group_reply_target_matches_bootstrap_and_identified_rows(tmp_path: Path) -> None:
    path = tmp_path / "data" / "whatsapp_reply_targets.json"
    rewrite_from_self_instruction(path, individuals=None, groups=[("Family Group", "+85212345678")])

    rows = load_group_reply_targets(path)
    bootstrap = match_group_reply_target(
        rows,
        group_id="",
        group_name="Family Group",
        member_id="alice@lid",
        member_phone="+85212345678",
    )
    assert bootstrap is not None
    assert bootstrap[1].group_name == "Family Group"

    observe_group_identification(
        path,
        group_name="Family Group",
        member_phone="+85212345678",
        group_id="1203630group@g.us",
        member_id="alice@lid",
        member_label="Alice",
    )
    rows = load_group_reply_targets(path)
    identified = match_group_reply_target(
        rows,
        group_id="1203630group@g.us",
        group_name="",
        member_id="alice@lid",
        member_phone="+85212345678",
    )
    assert identified is not None
    assert identified[1].group_id == "1203630group@g.us"
    assert identified[1].member_label == "Alice"


def test_load_reply_targets_migrates_legacy_contacts_once(tmp_path: Path) -> None:
    save_contacts(
        str(tmp_path / "data" / "contacts" / "whatsapp.json"),
        [
            WhatsAppContact(phone="+85212345678", label="Alice Wong", enabled=True),
            WhatsAppContact(phone="+85299990000", label="Disabled", enabled=False),
        ],
    )
    path = tmp_path / "data" / "whatsapp_reply_targets.json"

    payload = load_reply_targets(path, project_root=tmp_path)

    assert payload["migrations"]["contacts_file_imported"] is True
    assert [row["phone"] for row in payload["direct_reply_targets"]] == ["85212345678"]
    assert payload["direct_reply_targets"][0]["label"] == "Alice Wong"

    save_contacts(
        str(tmp_path / "data" / "contacts" / "whatsapp.json"),
        [WhatsAppContact(phone="+85277776666", label="New Contact", enabled=True)],
    )
    reloaded = load_reply_targets(path, project_root=tmp_path)
    assert [row["phone"] for row in reloaded["direct_reply_targets"]] == ["85212345678"]


def test_load_reply_targets_optionally_migrates_legacy_group_members_once(tmp_path: Path) -> None:
    legacy_groups = tmp_path / "groups.csv"
    save_group_members(
        str(legacy_groups),
        [
            WhatsAppGroupMember(
                group_id="1203630group@g.us",
                group_name="Insurance sales",
                member_id="alice@lid",
                member_pn="+85269432591",
                member_label="Alice",
                enabled=True,
            )
        ],
    )
    path = tmp_path / "data" / "whatsapp_reply_targets.json"

    payload = load_reply_targets(path, group_members_file=str(legacy_groups))

    assert payload["migrations"]["group_members_file_imported"] is True
    assert len(payload["group_reply_targets"]) == 1
    assert payload["group_reply_targets"][0]["group_name"] == "Insurance sales"
    assert payload["group_reply_targets"][0]["member_phone"] == "85269432591"

    save_group_members(
        str(legacy_groups),
        [
            WhatsAppGroupMember(
                group_id="new-group@g.us",
                group_name="New Group",
                member_id="new@lid",
                member_pn="+85260001111",
                member_label="New",
                enabled=True,
            )
        ],
    )
    reloaded = load_reply_targets(path, group_members_file=str(legacy_groups))
    assert len(reloaded["group_reply_targets"]) == 1
    assert reloaded["group_reply_targets"][0]["group_name"] == "Insurance sales"


def test_rewrite_from_self_instruction_preserves_existing_metadata(tmp_path: Path) -> None:
    path = tmp_path / "data" / "whatsapp_reply_targets.json"
    payload = load_reply_targets(path)
    payload["direct_reply_targets"] = [
        {
            "phone": "85212345678",
            "enabled": True,
            "label": "Alice Wong",
            "chat_id": "85212345678@s.whatsapp.net",
            "sender_id": "85212345678@s.whatsapp.net",
            "push_name": "Alice",
            "auto_draft": True,
            "last_seen_at": "2026-04-11T00:00:00+00:00",
        }
    ]
    payload["group_reply_targets"] = [
        {
            "group_name": "Insurance sales",
            "group_name_normalized": "insurance sales",
            "group_id": "1203630group@g.us",
            "member_phone": "85269432591",
            "member_id": "alice@lid",
            "member_label": "Alice",
            "enabled": True,
            "last_seen_at": "2026-04-11T00:00:00+00:00",
        }
    ]
    save_reply_targets(path, payload)

    rewrite_from_self_instruction(
        path,
        individuals=["+85212345678"],
        groups=[("Insurance sales", "+85269432591")],
    )
    reloaded = load_reply_targets(path)
    direct = reloaded["direct_reply_targets"][0]
    group = reloaded["group_reply_targets"][0]

    assert direct["label"] == "Alice Wong"
    assert direct["push_name"] == "Alice"
    assert direct["auto_draft"] is True
    assert group["group_id"] == "1203630group@g.us"
    assert group["member_id"] == "alice@lid"
    assert group["member_label"] == "Alice"
