"""Deterministic text privacy sanitizer for cloud-bound LLM payloads."""

from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanobot.config.schema import PrivacyGatewayConfig

UNKNOWN_PHONE = "Unknown Phone Number"
UNKNOWN_SENDER_NAME = "Unknown Sender Name"
UNKNOWN_TICKET = "Unknown Insurance Ticket Number"
UNKNOWN_CHAT_ID = "Unknown Chat ID"
UNKNOWN_GROUP_NAME = "Unknown Group Name"
UNKNOWN_OCCUPATION = "Unknown Occupation"
UNKNOWN_ADDRESS = "Unknown Living Address"
UNKNOWN_FAMILY_NAME = "Unknown Family Member Name"

_TEXT_BLOCK_TYPES = {"text", "input_text", "output_text"}
_STRUCTURED_FIELD_PATTERNS = (
    ("sender_name", re.compile(r"(?im)^(?P<prefix>\s*Sender Name:\s*)(?P<value>.+?)\s*$"), UNKNOWN_SENDER_NAME),
    ("sender_phone", re.compile(r"(?im)^(?P<prefix>\s*Sender Phone:\s*)(?P<value>.+?)\s*$"), UNKNOWN_PHONE),
    ("group_name", re.compile(r"(?im)^(?P<prefix>\s*Group Name:\s*)(?P<value>.+?)\s*$"), UNKNOWN_GROUP_NAME),
    ("chat_id", re.compile(r"(?im)^(?P<prefix>\s*Chat ID:\s*)(?P<value>.+?)\s*$"), UNKNOWN_CHAT_ID),
    ("occupation", re.compile(r"(?im)^(?P<prefix>\s*(?:Occupation|Job|職業|职业|任職|任职)\s*:\s*)(?P<value>.+?)\s*$"), UNKNOWN_OCCUPATION),
    ("address", re.compile(r"(?im)^(?P<prefix>\s*(?:Address|住址|地址|居住地)\s*:\s*)(?P<value>.+?)\s*$"), UNKNOWN_ADDRESS),
)
_JSON_FIELD_PATTERNS = (
    (re.compile(r'(?i)("sender_name"\s*:\s*")(?P<value>[^"\n]+)(")'), UNKNOWN_SENDER_NAME),
    (re.compile(r'(?i)("sender_phone"\s*:\s*")(?P<value>[^"\n]+)(")'), UNKNOWN_PHONE),
    (re.compile(r'(?i)("group_name"\s*:\s*")(?P<value>[^"\n]+)(")'), UNKNOWN_GROUP_NAME),
    (re.compile(r'(?i)("chat_id"\s*:\s*")(?P<value>[^"\n]+)(")'), UNKNOWN_CHAT_ID),
    (re.compile(r'(?i)("occupation"\s*:\s*")(?P<value>[^"\n]+)(")'), UNKNOWN_OCCUPATION),
    (re.compile(r'(?i)("address"\s*:\s*")(?P<value>[^"\n]+)(")'), UNKNOWN_ADDRESS),
)
_JSON_KEY_PLACEHOLDERS = {
    "sender_name": UNKNOWN_SENDER_NAME,
    "sender_phone": UNKNOWN_PHONE,
    "group_name": UNKNOWN_GROUP_NAME,
    "chat_id": UNKNOWN_CHAT_ID,
    "occupation": UNKNOWN_OCCUPATION,
    "address": UNKNOWN_ADDRESS,
    "living_address": UNKNOWN_ADDRESS,
    "insurance_ticket_number": UNKNOWN_TICKET,
    "policy_number": UNKNOWN_TICKET,
    "family_member_name": UNKNOWN_FAMILY_NAME,
}
_PHONE_PATTERN = re.compile(r"(?<!\w)(?:\+?\d[\d\s().-]{6,}\d)(?!\w)")
_CHAT_ID_PATTERN = re.compile(r"\b(?:\d{6,}@(?!example)(?:g\.us|s\.whatsapp\.net|c\.us)|[A-Za-z0-9._-]+@lid)\b")
_POLICY_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b(?:policy(?:\s*(?:no|number))?|ticket(?:\s*(?:no|number))?|insurance ticket number|"
    r"保單(?:號|编号|編號)?|保单(?:号|编号|編號)?|單號|单号|編號|编号|reference)\b\s*[:：#]?\s*)"
    r"(?P<value>(?=[A-Z0-9._/-]{5,}\b)(?=[A-Z0-9._/-]*\d)[A-Z0-9][A-Z0-9._/-]{4,})"
)
_ADDRESS_LINE_PATTERN = re.compile(
    r"(?i)\b(?:(?:room|flat|floor|block|tower)\s+\d[\w-]*|"
    r"\d+\s+(?:road|street|avenue|ave|district))\b[^\n,;。！？!?]{0,60}"
)
_ADDRESS_CJK_PATTERN = re.compile(
    r"\d+[A-Za-z-]*\s*(?:室|樓|层|層|座|大廈|大厦|街|路|區|区)[^\n,;。！？!?]{0,40}"
)
_ADDRESS_CUES = (
    re.compile(
        r"(?i)(?P<prefix>\b(?:address|live in|living in|reside in|residing at|located at)\b\s*[:：]?\s*)"
        r"(?P<value>.*?)(?=(?:,?\s+(?:and|but)\s+(?:i|my)\b|[\n。！？!?;；]|$))"
    ),
    re.compile(
        r"(?P<prefix>(?:住喺|住在|住於|住于|地址係|地址是|住址係|住址是|居住地係|居住地是)\s*)"
        r"(?P<value>.*?)(?=(?:，?(?:我|但|不過)|[\n。！？!?;；]|$))"
    ),
)
_OCCUPATION_WORDS = (
    "teacher", "engineer", "nurse", "manager", "sales", "consultant", "doctor", "lawyer",
    "accountant", "banker", "student", "driver", "clerk", "designer", "developer",
    "顧問", "经理", "經理", "老師", "医生", "醫生", "護士", "护士", "工程師", "工程师",
    "銀行", "银行", "司機", "司机", "學生", "学生", "銷售", "销售", "會計", "会计",
)
_OCCUPATION_CUES = (
    re.compile(r"(?i)(?P<prefix>\b(?:occupation|job|profession|works? as|working as|my job is)\b\s*[:：]?\s*)(?P<value>[^\n,.;，。！？!?]{1,40})"),
    re.compile(r"(?P<prefix>(?:我係|我是|我做|任職|任职|職業係|职业是|工作係|工作是)\s*)(?P<value>[^\n,.;，。！？!?]{1,20})"),
)
_FAMILY_CUES = (
    re.compile(
        r"(?P<prefix>\b(?i:wife|husband|spouse|son|daughter|child|mother|father|brother|sister|partner)\b"
        r"(?:\s+(?i:named|called|is))?\s*)(?P<value>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})\b"
    ),
    re.compile(
        r"(?P<prefix>(?:太太|老婆|老公|兒子|儿子|女兒|女儿|媽媽|妈妈|爸爸|父親|父亲|母親|母亲|哥哥|弟弟|姐姐|妹妹)"
        r"(?:叫|係|是)?\s*)(?P<value>[\u4e00-\u9fff]{2,4})"
    ),
)
_FAMILY_NAME_LINE = re.compile(
    r"(?i)(?P<prefix>\b(?:family member name|dependent name)\b\s*[:：]\s*)(?P<value>[^\n,.;，。！？!?]{1,40})"
)
_UNKNOWN_RE = re.compile(r"Unknown (?:Phone Number|Sender Name|Insurance Ticket Number|Chat ID|Group Name|Occupation|Living Address|Family Member Name)")

# Filesystem-path pattern: matches /Users/<user>/… or /home/<user>/… absolute paths.
# Replaces the home prefix up to and including the first path component after home directory.
UNKNOWN_PATH = "[HOME]"
_HOME_PATH_PATTERN = re.compile(
    r"(?:/Users/[A-Za-z0-9._-]+|/home/[A-Za-z0-9._-]+|"
    r"C:\\Users\\[A-Za-z0-9._-]+|C:/Users/[A-Za-z0-9._-]+)"
    r"(?:[/\\][^\s\"'`,;)}\]>]+)?"
)


@dataclass
class SanitizationResult:
    """Sanitized provider request plus audit metadata."""

    sanitized_payload: dict[str, Any]
    session_key: str
    placeholder_map: dict[str, str] = field(default_factory=dict)
    blocked: bool = False
    reasons: list[str] = field(default_factory=list)


class TextPrivacySanitizer:
    """Sanitize text payloads before they leave the local machine.

    Privacy pipeline step 5 in ``PRIVACY_PIPELINE.md``.
    The sanitizer is deterministic: it uses structured-field rules, regex
    matching, and a session-scoped placeholder cache rather than another model.
    """

    def __init__(self, config: PrivacyGatewayConfig, *, known_names: set[str] | None = None):
        self.config = config
        self._session_cache: dict[str, dict[str, str]] = defaultdict(dict)
        # Names that should always be redacted (loaded from contacts / reply targets).
        self._known_names: set[str] = {n for n in (known_names or set()) if n and len(n) >= 2}

    def sanitize_chat_payload(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> SanitizationResult:
        body = copy.deepcopy(payload)
        messages = body.get("messages")
        # The session key lets repeated private tokens be masked consistently
        # across later turns and local debug snapshots.
        session_key = self._extract_session_key(messages, headers=headers)
        placeholder_map = dict(self._session_cache.get(session_key, {}))
        reasons: list[str] = []

        # First sanitize every message and then sanitize any other top-level
        # payload fields that may also carry private values.
        if isinstance(messages, list):
            body["messages"] = [self._sanitize_message(m, placeholder_map, reasons) for m in messages]
        for key, value in list(body.items()):
            if key == "messages":
                continue
            if isinstance(value, dict):
                body[key] = self._sanitize_json_like(value, placeholder_map, reasons)
            elif isinstance(value, list):
                body[key] = self._sanitize_list(value, placeholder_map, reasons)
            elif isinstance(value, str) and key.lower() in _JSON_KEY_PLACEHOLDERS:
                placeholder = _JSON_KEY_PLACEHOLDERS[key.lower()]
                self._remember_token(placeholder_map, value, placeholder)
                body[key] = placeholder

    # Validation is a second pass: after masking, look again for anything
    # that still resembles raw private data.
        validation = self._validate_payload(body, placeholder_map)
        reasons.extend(validation)

        if placeholder_map:
            self._session_cache[session_key].update(placeholder_map)

        blocked = bool(reasons) and self.config.fail_closed
        return SanitizationResult(
            sanitized_payload=body,
            session_key=session_key,
            placeholder_map=placeholder_map,
            blocked=blocked,
            reasons=reasons,
        )

    def redact_text_for_debug(self, text: str, session_key: str = "debug") -> tuple[str, dict[str, str], list[str]]:
        """Sanitize one text blob using the same rules as cloud-bound payloads."""
        placeholder_map = dict(self._session_cache.get(session_key, {}))
        reasons: list[str] = []
        clean = self._sanitize_text(text, placeholder_map, reasons)
        reasons.extend(self._validate_text(clean, placeholder_map))
        return clean, placeholder_map, reasons

    @staticmethod
    def build_blocked_response(
        *,
        model: str,
        message: str | None = None,
    ) -> dict[str, Any]:
        """Return a minimal OpenAI-compatible completion for blocked requests."""
        content = message or (
            "I can't send this request to the cloud model because it still contains private details. "
            "Please restate it without names, phone numbers, ticket numbers, addresses, occupations, "
            "family member names, chat IDs, or group names."
        )
        return {
            "id": "chatcmpl-privacy-blocked",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def _sanitize_message(
        self,
        message: dict[str, Any],
        placeholder_map: dict[str, str],
        reasons: list[str],
    ) -> dict[str, Any]:
        clean = copy.deepcopy(message)
        content = clean.get("content")
        if isinstance(content, str):
            clean["content"] = self._sanitize_text(content, placeholder_map, reasons)
        elif isinstance(content, list):
            new_items: list[Any] = []
            for item in content:
                if not isinstance(item, dict):
                    new_items.append(item)
                    continue
                block = dict(item)
                item_type = str(block.get("type", ""))
                if item_type in _TEXT_BLOCK_TYPES and isinstance(block.get("text"), str):
                    block["text"] = self._sanitize_text(block["text"], placeholder_map, reasons)
                elif item_type == "image_url" and not self.config.text_only_scope:
                    block = block
                new_items.append(block)
            clean["content"] = new_items
        elif isinstance(content, dict):
            clean["content"] = self._sanitize_json_like(content, placeholder_map, reasons)
        return clean

    def _sanitize_json_like(
        self,
        payload: dict[str, Any],
        placeholder_map: dict[str, str],
        reasons: list[str],
    ) -> dict[str, Any]:
        clean: dict[str, Any] = {}
        for key, value in payload.items():
            key_placeholder = _JSON_KEY_PLACEHOLDERS.get(str(key).lower())
            if isinstance(value, str):
                if key_placeholder:
                    self._remember_token(placeholder_map, value, key_placeholder)
                    clean[key] = key_placeholder
                else:
                    clean[key] = self._sanitize_text(value, placeholder_map, reasons)
            elif isinstance(value, dict):
                clean[key] = self._sanitize_json_like(value, placeholder_map, reasons)
            elif isinstance(value, list):
                clean[key] = self._sanitize_list(value, placeholder_map, reasons, key_placeholder=key_placeholder)
            else:
                clean[key] = value
        return clean

    def _sanitize_list(
        self,
        items: list[Any],
        placeholder_map: dict[str, str],
        reasons: list[str],
        *,
        key_placeholder: str | None = None,
    ) -> list[Any]:
        clean_items: list[Any] = []
        for item in items:
            if isinstance(item, dict):
                clean_items.append(self._sanitize_json_like(item, placeholder_map, reasons))
            elif isinstance(item, str):
                if key_placeholder:
                    self._remember_token(placeholder_map, item, key_placeholder)
                    clean_items.append(key_placeholder)
                else:
                    clean_items.append(self._sanitize_text(item, placeholder_map, reasons))
            else:
                clean_items.append(item)
        return clean_items

    def _sanitize_text(
        self,
        text: str,
        placeholder_map: dict[str, str],
        reasons: list[str],
    ) -> str:
        clean = text
        # Ordered masking pipeline:
        # 1) exact previously seen tokens
        # 2) structured field replacements
        # 3) JSON-like field replacements
        # 4) free-text patterns such as policy IDs, phones, chat IDs, addresses,
        #    occupations, and family-member names
        # 5) exact-token pass again to catch repeats revealed by new mappings
        clean = self._apply_exact_placeholders(clean, placeholder_map)
        clean = self._replace_filesystem_paths(clean, placeholder_map)
        clean = self._replace_known_names(clean, placeholder_map)
        clean = self._sanitize_structured_fields(clean, placeholder_map)
        clean = self._sanitize_json_fields(clean, placeholder_map)
        clean = self._replace_pattern(clean, _POLICY_PATTERN, UNKNOWN_TICKET, placeholder_map)
        clean = self._replace_phones(clean, placeholder_map)
        clean = self._replace_chat_ids(clean, placeholder_map)
        clean = self._replace_addresses(clean, placeholder_map)
        clean = self._replace_occupations(clean, placeholder_map, reasons)
        clean = self._replace_family_names(clean, placeholder_map, reasons)
        clean = self._apply_exact_placeholders(clean, placeholder_map)
        return clean

    def _sanitize_structured_fields(self, text: str, placeholder_map: dict[str, str]) -> str:
        clean = text
        for _field, pattern, placeholder in _STRUCTURED_FIELD_PATTERNS:
            def _repl(match: re.Match[str]) -> str:
                value = match.group("value").strip()
                self._remember_token(placeholder_map, value, placeholder)
                return f"{match.group('prefix')}{placeholder}"

            clean = pattern.sub(_repl, clean)
        return clean

    def _sanitize_json_fields(self, text: str, placeholder_map: dict[str, str]) -> str:
        clean = text
        for pattern, placeholder in _JSON_FIELD_PATTERNS:
            def _repl(match: re.Match[str]) -> str:
                value = match.group("value").strip()
                self._remember_token(placeholder_map, value, placeholder)
                return f"{match.group(1)}{placeholder}{match.group(3)}"

            clean = pattern.sub(_repl, clean)
        return clean

    def _replace_phones(self, text: str, placeholder_map: dict[str, str]) -> str:
        def _repl(match: re.Match[str]) -> str:
            raw = match.group(0)
            if _looks_like_datetime_fragment(raw):
                return raw
            digits = "".join(ch for ch in raw if ch.isdigit())
            if len(digits) < 8:
                return raw
            self._remember_token(placeholder_map, raw, UNKNOWN_PHONE)
            self._remember_token(placeholder_map, digits, UNKNOWN_PHONE)
            return UNKNOWN_PHONE

        return _PHONE_PATTERN.sub(_repl, text)

    def _replace_chat_ids(self, text: str, placeholder_map: dict[str, str]) -> str:
        def _repl(match: re.Match[str]) -> str:
            raw = match.group(0)
            self._remember_token(placeholder_map, raw, UNKNOWN_CHAT_ID)
            return UNKNOWN_CHAT_ID

        return _CHAT_ID_PATTERN.sub(_repl, text)

    def _replace_filesystem_paths(self, text: str, placeholder_map: dict[str, str]) -> str:
        """Replace absolute home-directory paths with a generic placeholder."""
        def _repl(match: re.Match[str]) -> str:
            raw = match.group(0)
            self._remember_token(placeholder_map, raw, UNKNOWN_PATH)
            return UNKNOWN_PATH

        return _HOME_PATH_PATTERN.sub(_repl, text)

    def _replace_known_names(self, text: str, placeholder_map: dict[str, str]) -> str:
        """Replace pre-loaded known names (from contacts/reply targets) with a placeholder."""
        clean = text
        for name in sorted(self._known_names, key=len, reverse=True):
            if name in clean:
                self._remember_token(placeholder_map, name, UNKNOWN_SENDER_NAME)
                clean = clean.replace(name, UNKNOWN_SENDER_NAME)
        return clean

    def _replace_addresses(self, text: str, placeholder_map: dict[str, str]) -> str:
        clean = text
        for pattern in _ADDRESS_CUES:
            def _repl(match: re.Match[str]) -> str:
                raw = match.group("value").strip()
                self._remember_token(placeholder_map, raw, UNKNOWN_ADDRESS)
                return f"{match.group('prefix')}{UNKNOWN_ADDRESS}"

            clean = pattern.sub(_repl, clean)

        def _addr_line(match: re.Match[str]) -> str:
            raw = match.group(0)
            if raw == UNKNOWN_ADDRESS:
                return raw
            if not any(ch.isdigit() for ch in raw) and raw.count(" ") < 1:
                return raw
            self._remember_token(placeholder_map, raw.strip(), UNKNOWN_ADDRESS)
            return UNKNOWN_ADDRESS

        clean = _ADDRESS_LINE_PATTERN.sub(_addr_line, clean)
        clean = _ADDRESS_CJK_PATTERN.sub(_addr_line, clean)
        return clean

    def _replace_occupations(
        self,
        text: str,
        placeholder_map: dict[str, str],
        reasons: list[str],
    ) -> str:
        clean = text
        for pattern in _OCCUPATION_CUES:
            def _repl(match: re.Match[str]) -> str:
                raw = match.group("value").strip()
                if not raw:
                    reasons.append("occupation cue without value")
                    return match.group(0)
                self._remember_token(placeholder_map, raw, UNKNOWN_OCCUPATION)
                return f"{match.group('prefix')}{UNKNOWN_OCCUPATION}"

            clean = pattern.sub(_repl, clean)
        return clean

    def _replace_family_names(
        self,
        text: str,
        placeholder_map: dict[str, str],
        reasons: list[str],
    ) -> str:
        clean = text

        def _line_repl(match: re.Match[str]) -> str:
            raw = match.group("value").strip()
            self._remember_token(placeholder_map, raw, UNKNOWN_FAMILY_NAME)
            return f"{match.group('prefix')}{UNKNOWN_FAMILY_NAME}"

        clean = _FAMILY_NAME_LINE.sub(_line_repl, clean)
        for pattern in _FAMILY_CUES:
            def _repl(match: re.Match[str]) -> str:
                raw = match.group("value").strip()
                if not raw:
                    reasons.append("family cue without value")
                    return match.group(0)
                self._remember_token(placeholder_map, raw, UNKNOWN_FAMILY_NAME)
                return f"{match.group('prefix')}{UNKNOWN_FAMILY_NAME}"

            clean = pattern.sub(_repl, clean)
        return clean

    def _replace_pattern(
        self,
        text: str,
        pattern: re.Pattern[str],
        placeholder: str,
        placeholder_map: dict[str, str],
    ) -> str:
        def _repl(match: re.Match[str]) -> str:
            raw = match.group("value").strip()
            self._remember_token(placeholder_map, raw, placeholder)
            return f"{match.group('prefix')}{placeholder}"

        return pattern.sub(_repl, text)

    @staticmethod
    def _remember_token(placeholder_map: dict[str, str], raw: str, placeholder: str) -> None:
        token = raw.strip()
        if not token or _UNKNOWN_RE.fullmatch(token):
            return
        placeholder_map[token] = placeholder

    @staticmethod
    def _apply_exact_placeholders(text: str, placeholder_map: dict[str, str]) -> str:
        clean = text
        for raw, placeholder in sorted(placeholder_map.items(), key=lambda item: len(item[0]), reverse=True):
            if not raw or raw == placeholder:
                continue
            clean = clean.replace(raw, placeholder)
        return clean

    def _validate_payload(
        self,
        payload: dict[str, Any],
        placeholder_map: dict[str, str],
    ) -> list[str]:
        # Fail-closed support: collect residual-risk reasons after sanitization.
        reasons: list[str] = []
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return reasons
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                reasons.extend(self._validate_text(content, placeholder_map))
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") in _TEXT_BLOCK_TYPES and isinstance(item.get("text"), str):
                        reasons.extend(self._validate_text(item["text"], placeholder_map))
            elif isinstance(content, dict):
                reasons.extend(self._validate_value(content, placeholder_map))
        for key, value in payload.items():
            if key == "messages":
                continue
            reasons.extend(self._validate_value(value, placeholder_map))
        deduped: list[str] = []
        for reason in reasons:
            if reason not in deduped:
                deduped.append(reason)
        return deduped

    def _validate_value(self, value: Any, placeholder_map: dict[str, str]) -> list[str]:
        if isinstance(value, str):
            return self._validate_text(value, placeholder_map)
        if isinstance(value, dict):
            reasons: list[str] = []
            for key, item in value.items():
                key_placeholder = _JSON_KEY_PLACEHOLDERS.get(str(key).lower())
                if isinstance(item, str) and key_placeholder:
                    if self._normalized_candidate(item) != key_placeholder:
                        reasons.append(f"json field still present: {key}")
                    continue
                reasons.extend(self._validate_value(item, placeholder_map))
            return reasons
        if isinstance(value, list):
            reasons: list[str] = []
            for item in value:
                reasons.extend(self._validate_value(item, placeholder_map))
            return reasons
        return []

    def _validate_text(self, text: str, placeholder_map: dict[str, str]) -> list[str]:
        reasons: list[str] = []
        for raw, placeholder in placeholder_map.items():
            if raw and raw != placeholder and raw in text:
                reasons.append(f"unmasked seen token: {placeholder}")
        if _CHAT_ID_PATTERN.search(text):
            reasons.append("chat identifier still present")
        if _HOME_PATH_PATTERN.search(text):
            reasons.append("filesystem path still present")
        for match in _PHONE_PATTERN.finditer(text):
            if _looks_like_datetime_fragment(match.group(0)):
                continue
            digits = "".join(ch for ch in match.group(0) if ch.isdigit())
            if len(digits) >= 8:
                reasons.append("phone number still present")
                break
        if _POLICY_PATTERN.search(text.replace(UNKNOWN_TICKET, "")):
            reasons.append("policy or ticket number still present")
        for pattern in _ADDRESS_CUES:
            match = pattern.search(text)
            if not match:
                continue
            value = self._normalized_candidate(match.group("value"))
            if not value or len(value) < 3:
                continue
            if value != UNKNOWN_ADDRESS:
                reasons.append("address still present")
                break
        for pattern in _OCCUPATION_CUES:
            match = pattern.search(text)
            if match and self._normalized_candidate(match.group("value")) != UNKNOWN_OCCUPATION:
                reasons.append("occupation still present")
                break
        if _FAMILY_NAME_LINE.search(text):
            match = _FAMILY_NAME_LINE.search(text)
            if match and self._normalized_candidate(match.group("value")) != UNKNOWN_FAMILY_NAME:
                reasons.append("family member name still present")
        for pattern in _FAMILY_CUES:
            match = pattern.search(text)
            if match and self._normalized_candidate(match.group("value")) != UNKNOWN_FAMILY_NAME:
                reasons.append("family member name still present")
                break
        if _address_like_text_present(text):
            reasons.append("address-like text still present")
        for _field, pattern, placeholder in _STRUCTURED_FIELD_PATTERNS:
            for match in pattern.finditer(text):
                if self._normalized_candidate(match.group("value")) != placeholder:
                    reasons.append(f"structured field still present: {_field}")
        for pattern, placeholder in _JSON_FIELD_PATTERNS:
            for match in pattern.finditer(text):
                if self._normalized_candidate(match.group("value")) != placeholder:
                    reasons.append("json field still present")
        return reasons

    @staticmethod
    def _extract_session_key(
        messages: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> str:
        if isinstance(messages, list):
            for message in reversed(messages):
                text = TextPrivacySanitizer._extract_message_text(message)
                if not text:
                    continue
                channel = TextPrivacySanitizer._extract_runtime_value(text, "Channel")
                chat_id = TextPrivacySanitizer._extract_runtime_value(text, "Chat ID")
                if channel and chat_id:
                    return f"{channel}:{chat_id}"
        if headers:
            affinity = headers.get("x-session-affinity") or headers.get("X-Session-Affinity")
            if affinity:
                return f"affinity:{affinity}"
        return "global"

    @staticmethod
    def _extract_message_text(message: Any) -> str:
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") in _TEXT_BLOCK_TYPES and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(parts)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False)
        return ""

    @staticmethod
    def _extract_runtime_value(text: str, key: str) -> str:
        pattern = re.compile(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$")
        match = pattern.search(text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _normalized_candidate(value: str) -> str:
        return value.strip().strip("\"'")


def privacy_debug_dir(workspace: Path) -> Path:
    """Return the local directory used for privacy debug payloads.

    Note: the current implementation anchors this to the repo's ``test_words/``
    directory rather than deriving it from ``workspace``.
    """
    from nanobot.utils.paths import project_path
    return project_path("test_words")


def load_known_names(workspace: Path) -> set[str]:
    """Collect human names from whatsapp_reply_targets.json for privacy redaction.

    Returns a set of non-trivial name strings (>= 2 chars, not purely numeric).
    """
    names: set[str] = set()
    targets_file = workspace / "data" / "whatsapp_reply_targets.json"
    if not targets_file.is_file():
        return names
    try:
        data = json.loads(targets_file.read_text(encoding="utf-8"))
        for group in ("direct_reply_targets", "group_reply_targets"):
            for entry in data.get(group, []):
                push = str(entry.get("push_name") or "").strip()
                label = str(entry.get("label") or "").strip()
                for raw in (push, label):
                    # Skip empty, too-short, or purely-numeric values.
                    if raw and len(raw) >= 2 and not raw.replace(" ", "").replace("+", "").isdigit():
                        names.add(raw)
    except (json.JSONDecodeError, OSError):
        pass
    return names


def _address_like_text_present(text: str) -> bool:
    for match in _ADDRESS_LINE_PATTERN.finditer(text):
        raw = match.group(0)
        if raw != UNKNOWN_ADDRESS and any(ch.isdigit() for ch in raw):
            return True
    for match in _ADDRESS_CJK_PATTERN.finditer(text):
        raw = match.group(0)
        if raw != UNKNOWN_ADDRESS and any(ch.isdigit() for ch in raw):
            return True
    return False


def _looks_like_datetime_fragment(text: str) -> bool:
    value = text.strip()
    if not value:
        return False
    compact = re.sub(r"\s+", " ", value)
    # Avoid masking timestamps such as "2026-03-11 17" as phone numbers.
    return bool(re.fullmatch(r"(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}(?:\s+\d{1,2})?", compact))
