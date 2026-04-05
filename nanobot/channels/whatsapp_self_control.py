"""Parse and apply WhatsApp self-chat routing control messages."""

from __future__ import annotations

from dataclasses import dataclass

from nanobot.channels.whatsapp_contacts import (
    WhatsAppContact,
    load_contacts,
    normalize_contact_id,
    save_contacts,
)
from nanobot.channels.whatsapp_group_members import (
    WhatsAppGroupMember,
    load_group_members,
    normalize_group_name,
    save_group_members,
)
_INDIVIDUAL_MARKER = "#chatbot reply to individuals#"
_GROUP_MARKER = "#chatbot reply to groups#"


@dataclass(frozen=True)
class SelfRoutingInstruction:
    """Routing targets parsed from one self-chat control message."""

    individuals: list[str] | None = None
    groups: list[tuple[str, str]] | None = None


def parse_self_routing_instruction(text: str) -> SelfRoutingInstruction | None:
    """Parse the latest routing blocks from one self-chat message."""
    individuals_block = _extract_last_block(text, _INDIVIDUAL_MARKER)
    groups_block = _extract_last_block(text, _GROUP_MARKER)
    if individuals_block is None and groups_block is None:
        return None

    individuals = _parse_individual_lines(individuals_block) if individuals_block is not None else None
    groups = _parse_group_lines(groups_block) if groups_block is not None else None
    return SelfRoutingInstruction(individuals=individuals, groups=groups)


def apply_self_routing_instruction(
    *,
    contacts_file: str,
    group_members_file: str,
    instruction: SelfRoutingInstruction,
) -> dict[str, int]:
    """Apply parsed self-chat routing to local stores used by WhatsApp routing."""
    stats: dict[str, int] = {}

    if instruction.individuals is not None:
        existing_contacts = load_contacts(contacts_file) if contacts_file else []
        existing_by_phone = {normalize_contact_id(c.phone): c for c in existing_contacts if normalize_contact_id(c.phone)}
        contacts: list[WhatsAppContact] = []
        seen: set[str] = set()
        for raw_phone in instruction.individuals:
            phone = normalize_contact_id(raw_phone)
            if not phone or phone in seen:
                continue
            seen.add(phone)
            prev = existing_by_phone.get(phone)
            contacts.append(
                WhatsAppContact(
                    phone=phone,
                    label=prev.label if prev else "",
                    enabled=True,
                )
            )

        if contacts_file:
            save_contacts(contacts_file, contacts)
        stats["individual_count"] = len(contacts)

    if instruction.groups is not None:
        existing_rows = load_group_members(group_members_file) if group_members_file else []
        existing_by_key: dict[tuple[str, str], WhatsAppGroupMember] = {}
        for row in existing_rows:
            key = (normalize_group_name(row.group_name), normalize_contact_id(row.member_pn))
            if key[0] and key[1] and key not in existing_by_key:
                existing_by_key[key] = row

        rows: list[WhatsAppGroupMember] = []
        seen_group_members: set[tuple[str, str]] = set()
        for raw_group_name, raw_phone in instruction.groups:
            group_name = " ".join(raw_group_name.split())
            group_name_norm = normalize_group_name(group_name)
            member_pn = normalize_contact_id(raw_phone)
            if not group_name_norm or not member_pn:
                continue

            key = (group_name_norm, member_pn)
            if key in seen_group_members:
                continue
            seen_group_members.add(key)

            prev = existing_by_key.get(key)
            rows.append(
                WhatsAppGroupMember(
                    group_id=prev.group_id if prev else "",
                    group_name=prev.group_name if prev and prev.group_name else group_name,
                    member_id=prev.member_id if prev else "",
                    member_pn=prev.member_pn if prev and prev.member_pn else member_pn,
                    member_label=prev.member_label if prev else "",
                    enabled=True,
                )
            )

        if group_members_file:
            save_group_members(group_members_file, rows)
        stats["group_member_count"] = len(rows)

    return stats


def _extract_last_block(text: str, marker: str) -> str | None:
    marker_norm = marker.strip().casefold()
    in_block = False
    current_lines: list[str] = []
    blocks: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip().casefold()
        if line == marker_norm:
            if in_block:
                blocks.append("\n".join(current_lines).strip())
                current_lines = []
                in_block = False
            else:
                in_block = True
                current_lines = []
            continue

        if in_block:
            current_lines.append(raw_line)

    return blocks[-1] if blocks else None


def _parse_individual_lines(block: str) -> list[str]:
    phones: list[str] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        normalized = normalize_contact_id(line)
        if normalized:
            phones.append(normalized)
    return phones


def _parse_group_lines(block: str) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = line.replace("，", ",")
        if "," not in line:
            continue
        group_name, raw_phone = line.rsplit(",", 1)
        group_name = " ".join(group_name.split())
        member_pn = normalize_contact_id(raw_phone)
        if group_name and member_pn:
            items.append((group_name, member_pn))
    return items
