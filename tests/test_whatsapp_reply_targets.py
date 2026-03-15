from __future__ import annotations

from pathlib import Path

from nanobot.channels.whatsapp_reply_targets import (
    load_reply_targets,
    observe_direct_identification,
    observe_group_identification,
    rewrite_from_self_instruction,
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
