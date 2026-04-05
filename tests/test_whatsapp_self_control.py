from __future__ import annotations

from pathlib import Path

from nanobot.channels.whatsapp_contacts import WhatsAppContact, load_contacts, save_contacts
from nanobot.channels.whatsapp_group_members import (
    WhatsAppGroupMember,
    load_group_members,
    save_group_members,
)
from nanobot.channels.whatsapp_self_control import (
    apply_self_routing_instruction,
    parse_self_routing_instruction,
)


def test_parse_self_routing_instruction_uses_latest_block_and_parses_groups() -> None:
    text = """
    random note
    #chatbot reply to individuals#
    +85211112222
    #chatbot reply to individuals#

    #chatbot reply to groups#
    Insurance sales, +86 158 8725 0320
    Insurance sales，+852 6943 2591
    #chatbot reply to groups#

    #chatbot reply to individuals#
    +85233334444
    +85255556666
    #chatbot reply to individuals#
    """

    instruction = parse_self_routing_instruction(text)
    assert instruction is not None
    assert instruction.individuals == ["85233334444", "85255556666"]
    assert instruction.groups == [
        ("Insurance sales", "8615887250320"),
        ("Insurance sales", "85269432591"),
    ]


def test_apply_self_routing_instruction_rewrites_targets_and_preserves_known_ids(tmp_path: Path) -> None:
    contacts_file = str(tmp_path / "contacts.json")
    groups_file = str(tmp_path / "groups.csv")

    save_contacts(
        contacts_file,
        [
            WhatsAppContact(phone="+85212345678", label="Alice", enabled=True),
            WhatsAppContact(phone="+85299998888", label="Old", enabled=True),
        ],
    )
    save_group_members(
        groups_file,
        [
            WhatsAppGroupMember(
                group_id="1203630group@g.us",
                group_name="Insurance sales",
                member_id="alice@lid",
                member_pn="+8615887250320",
                member_label="Alice",
            ),
            WhatsAppGroupMember(
                group_id="old-group@g.us",
                group_name="Old Group",
                member_id="old@lid",
                member_pn="+85211110000",
                member_label="Old",
            ),
        ],
    )

    instruction = parse_self_routing_instruction(
        """
        #chatbot reply to individuals#
        +85212345678
        +85277776666
        #chatbot reply to individuals#
        #chatbot reply to groups#
        Insurance sales, +86 158 8725 0320
        #chatbot reply to groups#
        """
    )
    assert instruction is not None

    stats = apply_self_routing_instruction(
        contacts_file=contacts_file,
        group_members_file=groups_file,
        instruction=instruction,
    )

    contacts = load_contacts(contacts_file)
    rows = load_group_members(groups_file)

    assert stats["individual_count"] == 2
    assert stats["group_member_count"] == 1
    assert [c.phone for c in contacts] == ["85212345678", "85277776666"]
    assert contacts[0].label == "Alice"
    assert rows[0].group_id == "1203630group@g.us"
    assert rows[0].member_id == "alice@lid"
