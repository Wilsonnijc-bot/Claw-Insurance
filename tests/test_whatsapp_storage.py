import json
from pathlib import Path

from nanobot.channels.whatsapp_contacts import WhatsAppContact
from nanobot.channels.whatsapp_group_members import WhatsAppGroupMember
from nanobot.channels.whatsapp_storage import (
    session_file_path,
    storage_path,
    sync_direct_contact_storage,
    sync_group_row_storage,
)


def _write_session_file(path: Path, *, key: str, messages: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"_type": "metadata", "key": key, "metadata": {}, "last_consolidated": 0}) + "\n")
        for msg in messages:
            f.write(json.dumps(msg) + "\n")


def test_sync_direct_contact_storage_creates_metadata_and_materialized_history_export(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = storage_path("", workspace)
    session_path = session_file_path(workspace, "whatsapp:85212345678")
    _write_session_file(
        session_path,
        key="whatsapp:85212345678",
        messages=[
            {"role": "user", "content": "Hi", "timestamp": "2026-03-09T10:11:37.718083", "message_id": "m1"},
            {"role": "assistant", "content": "Hello", "timestamp": "2026-03-09T10:11:37.718099", "message_id": "m2"},
        ],
    )

    folder = sync_direct_contact_storage(
        root,
        workspace,
        WhatsAppContact(phone="+852 1234 5678", label="Alice", enabled=True),
        sender="85212345678@s.whatsapp.net",
        push_name="Alice Chan",
    )

    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["type"] == "direct"
    assert meta["normalized_phone"] == "85212345678"
    assert meta["session_key"] == "whatsapp:85212345678"
    assert Path(meta["session_file"]) == session_file_path(workspace, "whatsapp:85212345678")
    history_lines = [
        json.loads(line)
        for line in (folder / "history.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert not (folder / "history.jsonl").is_symlink()
    assert [item["role"] for item in history_lines] == ["client", "me"]
    assert [item["from_me"] for item in history_lines] == [False, True]


def test_sync_group_row_storage_bootstrap_marks_pending_until_ids_are_known(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = storage_path("", workspace)

    folder = sync_group_row_storage(
        root,
        workspace,
        1,
        WhatsAppGroupMember(
            group_id="",
            group_name="Insurance sales",
            member_id="",
            member_pn="+852 6943 2591",
            member_label="Prospect A",
            enabled=True,
        ),
    )

    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["type"] == "group"
    assert meta["row_number"] == 1
    assert meta["status"] == "bootstrap-pending"
    assert meta["session_key"] == ""
    assert not (folder / "history.jsonl").exists()


def test_sync_group_row_storage_links_history_after_group_id_is_known(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    root = storage_path("", workspace)

    folder = sync_group_row_storage(
        root,
        workspace,
        2,
        WhatsAppGroupMember(
            group_id="120363425808631928@g.us",
            group_name="Insurance sales",
            member_id="alice@lid",
            member_pn="+86 158 8725 0320",
            member_label="Alice",
            enabled=True,
        ),
    )

    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "active"
    assert meta["session_key"] == "whatsapp:120363425808631928@g.us:8615887250320"
    assert (folder / "history.jsonl").is_symlink()
