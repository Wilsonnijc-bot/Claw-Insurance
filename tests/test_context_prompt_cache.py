"""Tests for cache-friendly prompt construction."""

from __future__ import annotations

from datetime import datetime as real_datetime
from pathlib import Path
import datetime as datetime_module

from nanobot.agent.context import ContextBuilder
from nanobot.utils.helpers import sync_workspace_templates


class _FakeDatetime(real_datetime):
    current = real_datetime(2026, 2, 24, 13, 59)

    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return cls.current


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    return workspace


def test_system_prompt_stays_stable_when_clock_changes(tmp_path, monkeypatch) -> None:
    """System prompt should not change just because wall clock minute changes."""
    monkeypatch.setattr(datetime_module, "datetime", _FakeDatetime)

    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    _FakeDatetime.current = real_datetime(2026, 2, 24, 13, 59)
    prompt1 = builder.build_system_prompt()

    _FakeDatetime.current = real_datetime(2026, 2, 24, 14, 0)
    prompt2 = builder.build_system_prompt()

    assert prompt1 == prompt2


def test_runtime_context_is_separate_untrusted_user_message(tmp_path) -> None:
    """Runtime metadata should be merged with the user message."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Return exactly: OK",
        channel="cli",
        chat_id="direct",
    )

    assert messages[0]["role"] == "system"
    assert "## Current Session" not in messages[0]["content"]

    # Runtime context is now merged with user message into a single message
    assert messages[-1]["role"] == "user"
    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert ContextBuilder._RUNTIME_CONTEXT_TAG in user_content
    assert "Current Time:" in user_content
    assert "Channel: cli" in user_content
    assert "Chat ID: direct" in user_content
    assert "Return exactly: OK" in user_content


def test_whatsapp_direct_runtime_context_includes_sender_metadata(tmp_path) -> None:
    """WhatsApp direct chats should expose sender metadata in runtime context."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="Need help with term life insurance",
        channel="whatsapp",
        chat_id="85212345678@s.whatsapp.net",
        metadata={
            "is_group": False,
            "sender_name": "Alice Chan",
            "sender_phone": "+852 1234 5678",
        },
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert "Conversation Mode: whatsapp_direct" in user_content
    assert "Is Group: false" in user_content
    assert "Sender Name: Alice Chan" in user_content
    assert "Sender Phone: +852 1234 5678" in user_content


def test_whatsapp_group_runtime_context_includes_group_metadata(tmp_path) -> None:
    """WhatsApp group chats should expose group and sender metadata in runtime context."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="What coverage should I start with?",
        channel="whatsapp",
        chat_id="120363425808631928@g.us",
        metadata={
            "is_group": True,
            "group_name": "Insurance sales",
            "sender_name": "Bob",
            "sender_phone": "+86 158 8725 0320",
        },
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert "Conversation Mode: whatsapp_group" in user_content
    assert "Is Group: true" in user_content
    assert "Group Name: Insurance sales" in user_content
    assert "Sender Name: Bob" in user_content
    assert "Sender Phone: +86 158 8725 0320" in user_content


def test_runtime_context_includes_insurance_flow_state(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    messages = builder.build_messages(
        history=[],
        current_message="想了解牙科保障",
        channel="whatsapp",
        chat_id="120363425808631928@g.us",
        metadata={
            "is_group": True,
            "insurance_flow_mode": "skill",
            "insurance_generic_reply_count": 2,
            "insurance_cycle_active": True,
        },
    )

    user_content = messages[-1]["content"]
    assert isinstance(user_content, str)
    assert "Insurance Flow Mode: skill" in user_content
    assert "Insurance Generic Reply Count: 2" in user_content
    assert "Insurance Cycle Active: true" in user_content


def test_synced_templates_build_insurance_persona_prompt(tmp_path) -> None:
    """Workspace templates should encode the insurance persona and missing-facts guardrails."""
    workspace = _make_workspace(tmp_path)
    sync_workspace_templates(workspace, silent=True)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "# Operational Policy" in prompt
    assert "# Business Persona And Messaging" in prompt
    assert "# Operator Profile And Preferences" in prompt
    assert "# Current Tool Limitations" in prompt
    assert "professional insurance advisor on WhatsApp" in prompt
    assert "Traditional Chinese" in prompt
    assert "Mixed bilingual phrasing is allowed" in prompt
    assert "Direct chats can be consultative" in prompt
    assert "Group chats should be shorter" in prompt
    assert "Do not invent premiums" in prompt
    assert "ask one to three focused follow-up questions" in prompt
    assert "Prefer short paragraphs or compact natural sentences over bullet points" in prompt


def test_builtin_product_skills_appear_in_prompt_summary(tmp_path) -> None:
    """The product advisor and builtin Tavily skills should be discoverable in prompt context."""
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt()

    assert "<name>insurance-product-advisor</name>" in prompt
    assert "<name>tavily-search</name>" in prompt


def test_requested_skill_content_is_injected_into_system_prompt(tmp_path) -> None:
    workspace = _make_workspace(tmp_path)
    builder = ContextBuilder(workspace)

    prompt = builder.build_system_prompt(["insurance-product-advisor"])

    assert "# Requested Skills" in prompt
    assert "### Skill: insurance-product-advisor" in prompt
    assert "Apply this skill based on the runtime insurance flow state" in prompt
