from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model", memory_window=10)
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


def _wa_message(content: str) -> InboundMessage:
    return InboundMessage(
        channel="whatsapp",
        sender_id="8613136101623",
        chat_id="120363425808631928@g.us",
        content=content,
        metadata={"is_group": True, "sender_phone": "+86 131 3610 1623"},
    )


@pytest.mark.asyncio
async def test_generic_turns_switch_to_skill_mode_on_third_insurance_turn(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    captured_messages: list[list[dict]] = []
    responses = iter(
        [
            LLMResponse(content="可以，先講下你比較想睇醫療定儲蓄？"),
            LLMResponse(content="20歲都可以開始。你而家其實想先睇牙科、醫療，定人壽？"),
            LLMResponse(content="好，我先幫你收窄方向。你想先睇牙科、醫療、危疾、人壽，定儲蓄？"),
        ]
    )

    async def _chat(*args, **kwargs):
        captured_messages.append(copy.deepcopy(kwargs["messages"]))
        return next(responses)

    loop.provider.chat = AsyncMock(side_effect=_chat)

    await loop._process_message(_wa_message("我想了解保险"))
    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "generic"
    assert session.metadata["insurance_generic_reply_count"] == 1
    assert session.metadata["insurance_cycle_active"] is True

    await loop._process_message(_wa_message("20岁"))
    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "skill"
    assert session.metadata["insurance_generic_reply_count"] == 2
    assert session.metadata["insurance_cycle_active"] is True

    await loop._process_message(_wa_message("我想看牙科"))

    last_call = captured_messages[-1]
    assert "# Requested Skills" in last_call[0]["content"]
    assert "### Skill: insurance-product-advisor" in last_call[0]["content"]
    assert "Insurance Flow Mode: skill" in last_call[-1]["content"]
    assert "Insurance Generic Reply Count: 2" in last_call[-1]["content"]


@pytest.mark.asyncio
async def test_non_insurance_turn_does_not_increment_counter(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    loop.provider.chat = AsyncMock(
        side_effect=[
            LLMResponse(content="可以，先講下你比較想睇醫療定儲蓄？"),
            LLMResponse(content="今晚食咩就睇你口味，我就唔幫你計入保險流程。"),
        ]
    )

    await loop._process_message(_wa_message("我想了解保险"))
    await loop._process_message(_wa_message("今晚想同朋友去吃火锅，純粹聊天"))

    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "generic"
    assert session.metadata["insurance_generic_reply_count"] == 1


@pytest.mark.asyncio
async def test_domain_plus_two_facts_switches_to_skill_mode_immediately(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    captured_messages: list[list[dict]] = []

    async def _chat(*args, **kwargs):
        captured_messages.append(copy.deepcopy(kwargs["messages"]))
        return LLMResponse(content="我先按你而家條件幫你收窄幾個較貼近的牙科方案。")

    loop.provider.chat = AsyncMock(side_effect=_chat)

    await loop._process_message(_wa_message("我想睇牙科，30歲，住香港。"))

    first_call = captured_messages[-1]
    assert "# Requested Skills" in first_call[0]["content"]
    assert "### Skill: insurance-product-advisor" in first_call[0]["content"]
    assert "Insurance Flow Mode: skill" in first_call[-1]["content"]

    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "skill"
    assert session.metadata["insurance_generic_reply_count"] == 0


@pytest.mark.asyncio
async def test_skill_mode_with_missing_fields_stays_in_skill_mode(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    session.metadata.update(
        {
            "insurance_flow_mode": "skill",
            "insurance_generic_reply_count": 2,
            "insurance_cycle_active": True,
            "insurance_waiting_for_answer": True,
        }
    )
    loop.sessions.save(session)

    calls = iter(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="call1",
                        name="exec",
                        arguments={
                            "command": "python3 nanobot/skills/insurance-product-advisor/scripts/find_products.py --domain Dental --facts-file /tmp/facts.json"
                        },
                    )
                ],
            ),
            LLMResponse(content="可以，咁我只差一樣資料：你主要住喺香港、澳門，定其他地方？"),
        ]
    )
    loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))

    async def _execute(name: str, params: dict) -> str:
        assert name == "exec"
        return """
        {
          "domain": "dental",
          "missing_fields": [{"field": "residence_location", "question": "你主要住喺香港、澳門，定其他地方？"}],
          "candidates": []
        }
        """

    loop.tools.execute = AsyncMock(side_effect=_execute)

    await loop._process_message(_wa_message("我想直接推荐牙科计划"))

    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "skill"
    assert session.metadata["insurance_cycle_active"] is True


@pytest.mark.asyncio
async def test_research_completion_resets_to_generic_mode(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    session.metadata.update(
        {
            "insurance_flow_mode": "skill",
            "insurance_generic_reply_count": 2,
            "insurance_cycle_active": True,
            "insurance_waiting_for_answer": True,
        }
    )
    loop.sessions.save(session)

    calls = iter(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="find1",
                        name="exec",
                        arguments={
                            "command": "python3 nanobot/skills/insurance-product-advisor/scripts/find_products.py --domain Dental --facts-file /tmp/facts.json"
                        },
                    )
                ],
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="research1",
                        name="exec",
                        arguments={
                            "command": "python3 nanobot/skills/insurance-product-advisor/scripts/research_products.py --candidates-file /tmp/candidates.json"
                        },
                    )
                ],
            ),
            LLMResponse(content="我會先推兩三個較貼近你而家需要的牙科選擇。"),
        ]
    )
    loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))

    async def _execute(name: str, params: dict) -> str:
        command = params["command"]
        if "find_products.py" in command:
            return """
            {
              "domain": "dental",
              "missing_fields": [],
              "candidates": [{"plan_id": "d1", "plan_name": "Plan 1"}]
            }
            """
        return """
        {
          "candidates": [{"plan_id": "d1", "plan_name": "Plan 1", "brochure_research": {"summary": ["ok"]}}]
        }
        """

    loop.tools.execute = AsyncMock(side_effect=_execute)

    await loop._process_message(_wa_message("直接推荐牙科计划"))

    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "generic"
    assert session.metadata["insurance_generic_reply_count"] == 0
    assert session.metadata["insurance_cycle_active"] is False


@pytest.mark.asyncio
async def test_no_fit_completion_resets_to_generic_mode(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    session.metadata.update(
        {
            "insurance_flow_mode": "skill",
            "insurance_generic_reply_count": 2,
            "insurance_cycle_active": True,
            "insurance_waiting_for_answer": True,
        }
    )
    loop.sessions.save(session)

    calls = iter(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="find1",
                        name="exec",
                        arguments={
                            "command": "python3 nanobot/skills/insurance-product-advisor/scripts/find_products.py --domain Dental --facts-file /tmp/facts.json"
                        },
                    )
                ],
            ),
            LLMResponse(content="而家本地產品表入面未有一個真正貼合你條件的牙科選擇。"),
        ]
    )
    loop.provider.chat = AsyncMock(side_effect=lambda *a, **kw: next(calls))
    loop.tools.execute = AsyncMock(
        return_value="""
        {
          "domain": "dental",
          "missing_fields": [],
          "candidates": []
        }
        """
    )

    await loop._process_message(_wa_message("直接推荐牙科计划"))

    session = loop.sessions.get_or_create("whatsapp:120363425808631928@g.us")
    assert session.metadata["insurance_flow_mode"] == "generic"
    assert session.metadata["insurance_generic_reply_count"] == 0
    assert session.metadata["insurance_cycle_active"] is False
