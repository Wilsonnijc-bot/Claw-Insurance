from pathlib import Path

from nanobot.channels.whatsapp_group_members import (
    WhatsAppGroupMember,
    find_group_member_match,
    has_group_members_store,
    init_group_members_store,
    is_group_member_allowed,
    learn_group_member_identity,
    load_group_members,
    save_group_members,
)


def test_whatsapp_group_members_store_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "whatsapp_groups.csv"
    init_group_members_store(str(path))
    save_group_members(
        str(path),
        [
            WhatsAppGroupMember(
                group_id="1203630group@g.us",
                group_name="Family Group",
                member_id="alice@lid",
                member_pn="+85212345678",
                member_label="Alice",
            ),
            WhatsAppGroupMember(
                group_id="1203630group@g.us",
                group_name="Family Group",
                member_id="bob@lid",
                member_pn="+85287654321",
                member_label="Bob",
                enabled=False,
            ),
        ],
    )

    rows = load_group_members(str(path))

    assert has_group_members_store(str(path)) is True
    assert rows == [
        WhatsAppGroupMember(
            group_id="1203630group@g.us",
            group_name="Family Group",
            member_id="alice@lid",
            member_pn="+85212345678",
            member_label="Alice",
            enabled=True,
        ),
        WhatsAppGroupMember(
            group_id="1203630group@g.us",
            group_name="Family Group",
            member_id="bob@lid",
            member_pn="+85287654321",
            member_label="Bob",
            enabled=False,
        ),
    ]


def test_whatsapp_group_members_match_group_member_and_phone() -> None:
    rows = [
        WhatsAppGroupMember(
            group_id="1203630group@g.us",
            group_name="Family Group",
            member_id="alice@lid",
            member_pn="+85212345678",
            member_label="Alice",
        )
    ]

    assert is_group_member_allowed(
        "1203630group@g.us",
        "Family Group",
        "alice@lid",
        "+85212345678",
        rows,
    ) is True
    assert is_group_member_allowed(
        "1203630group@g.us",
        "Wrong Name",
        "alice@lid",
        "+85212345678",
        rows,
    ) is False
    assert is_group_member_allowed(
        "1203630group@g.us",
        "Family Group",
        "alice@lid",
        "+85200000000",
        rows,
    ) is False


def test_whatsapp_group_members_bootstrap_from_group_name_and_phone(tmp_path: Path) -> None:
    path = tmp_path / "whatsapp_groups.csv"
    save_group_members(
        str(path),
        [
            WhatsAppGroupMember(
                group_id="",
                group_name="Family Group",
                member_id="",
                member_pn="+85212345678",
                member_label="Alice",
            )
        ],
    )

    rows = load_group_members(str(path))
    assert find_group_member_match(
        "1203630group@g.us",
        "Family Group",
        "alice@lid",
        "+85212345678",
        rows,
    ) == 0

    changed = learn_group_member_identity(
        str(path),
        "1203630group@g.us",
        "Family Group",
        "alice@lid",
        "+85212345678",
    )

    assert changed is True
    assert load_group_members(str(path)) == [
        WhatsAppGroupMember(
            group_id="1203630group@g.us",
            group_name="Family Group",
            member_id="alice@lid",
            member_pn="+85212345678",
            member_label="Alice",
            enabled=True,
        )
    ]


def test_whatsapp_group_members_bootstrap_without_runtime_group_name_when_phone_is_unique() -> None:
    rows = [
        WhatsAppGroupMember(
            group_id="",
            group_name="Insurance sales",
            member_id="",
            member_pn="+8615887250320",
            member_label="Lead A",
        ),
        WhatsAppGroupMember(
            group_id="",
            group_name="Insurance sales",
            member_id="",
            member_pn="+85269432591",
            member_label="Lead B",
        ),
    ]

    assert find_group_member_match(
        "1203630group@g.us",
        "",
        "person@lid",
        "+85269432591",
        rows,
    ) == 1
