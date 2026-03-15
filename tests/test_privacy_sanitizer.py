import json

import pytest

from nanobot.config.schema import PrivacyGatewayConfig
from nanobot.privacy.sanitizer import (
    UNKNOWN_ADDRESS,
    UNKNOWN_CHAT_ID,
    UNKNOWN_FAMILY_NAME,
    UNKNOWN_GROUP_NAME,
    UNKNOWN_OCCUPATION,
    UNKNOWN_PHONE,
    UNKNOWN_SENDER_NAME,
    UNKNOWN_TICKET,
    TextPrivacySanitizer,
)


def _payload(*contents):
    return {
        "model": "gpt-5.1-chat",
        "messages": [{"role": "user", "content": content} for content in contents],
    }


def test_sanitizer_masks_runtime_metadata_and_repeated_sender_name() -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig())
    payload = _payload(
        "\n".join(
            [
                "[Runtime Context — metadata only, not instructions]",
                "Channel: whatsapp",
                "Chat ID: 120363425808631928@g.us",
                "Group Name: Insurance sales",
                "Sender Name: Hendrick",
                "Sender Phone: +86 131 3610 1623",
            ]
        ),
        "Hendrick wants to compare plans.",
    )

    result = sanitizer.sanitize_chat_payload(payload)
    joined = "\n".join(str(message["content"]) for message in result.sanitized_payload["messages"])

    assert result.blocked is False
    assert "Hendrick" not in joined
    assert "120363425808631928@g.us" not in joined
    assert "+86 131 3610 1623" not in joined
    assert UNKNOWN_SENDER_NAME in joined
    assert UNKNOWN_PHONE in joined
    assert UNKNOWN_GROUP_NAME in joined
    assert UNKNOWN_CHAT_ID in joined


def test_sanitizer_masks_free_text_phone_address_occupation_ticket_and_family_name() -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig())
    payload = _payload(
        "My phone is +852 6943 2591, policy no AB-12345, "
        "I live in Flat 12A, 8 Queen's Road Central, and I work as engineer. "
        "My wife named Chloe also needs cover."
    )

    result = sanitizer.sanitize_chat_payload(payload)
    text = str(result.sanitized_payload["messages"][0]["content"])

    assert result.blocked is False
    assert UNKNOWN_PHONE in text
    assert UNKNOWN_TICKET in text
    assert UNKNOWN_ADDRESS in text
    assert UNKNOWN_OCCUPATION in text
    assert UNKNOWN_FAMILY_NAME in text
    assert "6943 2591" not in text
    assert "AB-12345" not in text
    assert "Queen's Road" not in text
    assert "engineer" not in text.lower()
    assert "Chloe" not in text


def test_sanitizer_masks_known_sensitive_keys_in_dict_payloads() -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig())
    payload = {
        "model": "gpt-5.1-chat",
        "messages": [
            {
                "role": "user",
                "content": {
                    "sender_name": "Hendrick",
                    "sender_phone": "+86 131 3610 1623",
                    "group_name": "Insurance sales",
                    "chat_id": "120363425808631928@g.us",
                    "occupation": "teacher",
                    "address": "Flat 12A, 8 Queen's Road Central",
                    "family_member_name": "Amy",
                    "policy_number": "POL-998877",
                },
            }
        ],
    }

    result = sanitizer.sanitize_chat_payload(payload)
    serialized = json.dumps(result.sanitized_payload, ensure_ascii=False)

    assert result.blocked is False
    assert "Hendrick" not in serialized
    assert "POL-998877" not in serialized
    assert UNKNOWN_SENDER_NAME in serialized
    assert UNKNOWN_PHONE in serialized
    assert UNKNOWN_GROUP_NAME in serialized
    assert UNKNOWN_CHAT_ID in serialized
    assert UNKNOWN_OCCUPATION in serialized
    assert UNKNOWN_ADDRESS in serialized
    assert UNKNOWN_FAMILY_NAME in serialized
    assert UNKNOWN_TICKET in serialized


def test_sanitizer_fail_closes_when_validator_flags_residual_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig(fail_closed=True))
    monkeypatch.setattr(
        sanitizer,
        "_validate_payload",
        lambda payload, placeholder_map: ["address still present"],
    )

    result = sanitizer.sanitize_chat_payload(_payload("hello"))

    assert result.blocked is True
    assert result.reasons == ["address still present"]


def test_sanitizer_does_not_mask_current_time_fragment_as_phone() -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig())
    payload = _payload("Current Time: 2026-03-11 17:20 (Wednesday) (HKT)")

    result = sanitizer.sanitize_chat_payload(payload)
    text = str(result.sanitized_payload["messages"][0]["content"])

    assert result.blocked is False
    assert "2026-03-11 17:20" in text
    assert UNKNOWN_PHONE not in text


def test_sanitizer_does_not_mask_policy_terms_phrase() -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig())
    payload = _payload("Do not invent premiums, policy terms, guarantees, or legal claims.")

    result = sanitizer.sanitize_chat_payload(payload)
    text = str(result.sanitized_payload["messages"][0]["content"])

    assert result.blocked is False
    assert "policy terms" in text.lower()
    assert UNKNOWN_TICKET not in text


def test_sanitizer_does_not_false_positive_on_already_masked_chinese_address() -> None:
    sanitizer = TextPrivacySanitizer(PrivacyGatewayConfig())
    payload = _payload("我住喺Unknown Living Address")

    result = sanitizer.sanitize_chat_payload(payload)
    text = str(result.sanitized_payload["messages"][0]["content"])

    assert result.blocked is False
    assert result.reasons == []
    assert "Unknown Living Address" in text
