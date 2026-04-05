"""Local WhatsApp group-member allowlist backed by CSV."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from nanobot.channels.whatsapp_contacts import normalize_contact_id
from nanobot.utils.paths import confine_path, project_root


@dataclass(frozen=True)
class WhatsAppGroupMember:
    """One allowed WhatsApp member inside one group."""

    group_id: str
    group_name: str = ""
    member_id: str = ""
    member_pn: str = ""
    member_label: str = ""
    enabled: bool = True


def group_members_path(path_str: str) -> Path:
    """Return the expanded local group-members file path (project-confined)."""
    path = Path(path_str)
    if path.is_absolute():
        return confine_path(path)
    return confine_path(project_root() / path)


def init_group_members_store(path_str: str) -> Path:
    """Create the CSV file with a header if it does not exist yet."""
    path = group_members_path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["group_id", "group_name", "member_id", "member_pn", "member_label", "enabled"],
            )
            writer.writeheader()
    return path


def has_group_members_store(path_str: str) -> bool:
    """Return True when a local CSV allowlist exists."""
    return group_members_path(path_str).exists()


def normalize_group_id(value: str) -> str:
    """Normalize a WhatsApp group identifier."""
    return str(value or "").strip().lower()


def normalize_group_name(value: str) -> str:
    """Normalize a WhatsApp group name for loose comparison."""
    return " ".join(str(value or "").split()).casefold()


def normalize_member_id(value: str) -> str:
    """Normalize a WhatsApp member identifier."""
    return str(value or "").strip().lower()


def member_id_variants(value: str) -> set[str]:
    """Return exact and bare variants of a member identifier."""
    normalized = normalize_member_id(value)
    if not normalized:
        return set()

    variants = {normalized}
    if "@" in normalized:
        variants.add(normalized.split("@", 1)[0])
    return variants


def load_group_members(path_str: str) -> list[WhatsAppGroupMember]:
    """Load group-member rows from CSV. Missing file means no local store yet."""
    path = group_members_path(path_str)
    if not path.exists():
        return []

    rows: list[WhatsAppGroupMember] = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            group_id = str(raw.get("group_id", "")).strip()
            group_name = str(raw.get("group_name", "")).strip()
            member_id = str(raw.get("member_id", "")).strip()
            member_pn = str(raw.get("member_pn", "")).strip()
            if not normalize_group_id(group_id) and not normalize_group_name(group_name):
                continue
            if not normalize_member_id(member_id) and not normalize_contact_id(member_pn):
                continue

            enabled_value = str(raw.get("enabled", "true")).strip().lower()
            rows.append(
                WhatsAppGroupMember(
                    group_id=group_id,
                    group_name=group_name,
                    member_id=member_id,
                    member_pn=member_pn,
                    member_label=str(raw.get("member_label", "")).strip(),
                    enabled=enabled_value not in {"0", "false", "no", "off"},
                )
            )
    return rows


def save_group_members(path_str: str, rows: list[WhatsAppGroupMember]) -> Path:
    """Write the group-member CSV."""
    path = init_group_members_store(path_str)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["group_id", "group_name", "member_id", "member_pn", "member_label", "enabled"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "group_id": row.group_id,
                    "group_name": row.group_name,
                    "member_id": row.member_id,
                    "member_pn": row.member_pn,
                    "member_label": row.member_label,
                    "enabled": "true" if row.enabled else "false",
                }
            )
    return path


def find_group_member_match(
    group_id: str,
    group_name: str,
    member_id: str,
    member_pn: str,
    rows: list[WhatsAppGroupMember],
) -> int | None:
    """Return the first enabled row index that matches the incoming group member."""
    incoming_group_id = normalize_group_id(group_id)
    incoming_group_name = normalize_group_name(group_name)
    incoming_member_id_variants = member_id_variants(member_id)
    incoming_member_pn = normalize_contact_id(member_pn)

    if not incoming_group_id and not incoming_group_name:
        return None

    strict_matches: list[int] = []
    bootstrap_candidates: list[int] = []

    for index, row in enumerate(rows):
        if not row.enabled:
            continue
        if row.group_id and normalize_group_id(row.group_id) != incoming_group_id:
            continue
        if not row.group_id and not row.group_name:
            continue
        if row.group_name:
            normalized_row_group_name = normalize_group_name(row.group_name)
            if incoming_group_name:
                if normalized_row_group_name != incoming_group_name:
                    continue
            else:
                # Keep bootstrap rows as a fallback when WhatsApp cannot provide group metadata yet.
                if not row.group_id:
                    if row.member_id and normalize_member_id(row.member_id) not in incoming_member_id_variants:
                        continue
                    if row.member_pn and normalize_contact_id(row.member_pn) != incoming_member_pn:
                        continue
                    if not row.member_id and not row.member_pn:
                        continue
                    bootstrap_candidates.append(index)
                    continue
                continue
        if row.member_id and normalize_member_id(row.member_id) not in incoming_member_id_variants:
            continue
        if row.member_pn and normalize_contact_id(row.member_pn) != incoming_member_pn:
            continue
        if not row.member_id and not row.member_pn:
            continue
        strict_matches.append(index)

    if strict_matches:
        return strict_matches[0]
    if len(bootstrap_candidates) == 1:
        return bootstrap_candidates[0]
    return None


def is_group_member_allowed(
    group_id: str,
    group_name: str,
    member_id: str,
    member_pn: str,
    rows: list[WhatsAppGroupMember],
) -> bool:
    """Check whether an incoming group member matches one enabled CSV row."""
    return find_group_member_match(group_id, group_name, member_id, member_pn, rows) is not None


def match_group_member(
    group_id: str,
    group_name: str,
    member_id: str,
    member_pn: str,
    rows: list[WhatsAppGroupMember],
) -> tuple[int, WhatsAppGroupMember] | None:
    """Return the matching CSV row index and row when one exists."""
    match_index = find_group_member_match(group_id, group_name, member_id, member_pn, rows)
    if match_index is None:
        return None
    return match_index, rows[match_index]


def learn_group_member_identity(
    path_str: str,
    group_id: str,
    group_name: str,
    member_id: str,
    member_pn: str,
) -> bool:
    """Backfill missing IDs into the first matching bootstrap row."""
    rows = load_group_members(path_str)
    match_index = find_group_member_match(group_id, group_name, member_id, member_pn, rows)
    if match_index is None:
        return False

    row = rows[match_index]
    updated = WhatsAppGroupMember(
        group_id=row.group_id or group_id,
        group_name=row.group_name or group_name,
        member_id=row.member_id or member_id,
        member_pn=row.member_pn or member_pn,
        member_label=row.member_label,
        enabled=row.enabled,
    )

    if updated == row:
        return False

    rows[match_index] = updated
    save_group_members(path_str, rows)
    return True
