"""Agent loop: the core processing engine."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import weakref
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.agent.subagent import SubagentManager
from nanobot.agent.tools.cron import CronTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.message import MessageTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.spawn import SpawnTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import HistoryImportResult, InboundHistoryBatch, InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.privacy.sanitizer import TextPrivacySanitizer, load_known_names
from nanobot.providers.base import LLMProvider
from nanobot.session.manager import (
    Session,
    SessionManager,
    model_role_for_session,
    storage_role_for_session,
)

if TYPE_CHECKING:
    from nanobot.config.schema import ChannelsConfig, ExecToolConfig, PrivacyGatewayConfig
    from nanobot.cron.service import CronService


class AgentLoop:
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    _TOOL_RESULT_MAX_CHARS = 500
    _INSURANCE_FLOW_MODE_KEY = "insurance_flow_mode"
    _INSURANCE_GENERIC_REPLY_COUNT_KEY = "insurance_generic_reply_count"
    _INSURANCE_CYCLE_ACTIVE_KEY = "insurance_cycle_active"
    _INSURANCE_WAITING_FOR_ANSWER_KEY = "insurance_waiting_for_answer"
    _INSURANCE_GENERIC_LIMIT = 2
    _INSURANCE_SKILL_NAME = "insurance-product-advisor"
    _OFFLINE_MEETING_RUNTIME_KEY = "offline_meeting_notes"
    _OFFLINE_MEETING_CONTEXT_LIMIT = 3
    _INSURANCE_TOPIC_KEYWORDS = (
        "insurance",
        "insured",
        "medical",
        "health",
        "critical illness",
        "life protection",
        "life insurance",
        "savings",
        "retirement",
        "dental",
        "premium",
        "coverage",
        "benefit",
        "recommend",
        "recommendation",
        "compare",
        "comparison",
        "plan",
        "policy",
        "牙科",
        "醫療",
        "医疗",
        "危疾",
        "重疾",
        "人壽",
        "人寿",
        "保障",
        "保險",
        "保险",
        "儲蓄",
        "储蓄",
        "退休",
        "推薦",
        "推荐",
        "比較",
        "比较",
        "產品",
        "产品",
        "保費",
        "保费",
    )
    _INSURANCE_FOLLOWUP_KEYWORDS = (
        "hong kong",
        "hk",
        "macau",
        "macao",
        "香港",
        "澳門",
        "澳门",
        "牙科",
        "醫療",
        "医疗",
        "危疾",
        "重疾",
        "人壽",
        "人寿",
        "儲蓄",
        "储蓄",
        "退休",
        "個人",
        "个人",
        "團體",
        "团体",
        "公司",
        "僱員",
        "雇员",
        "學生",
        "学生",
        "工作",
        "家庭",
        "配偶",
        "beneficiary",
        "single",
        "family",
        "coverage",
        "amount",
        "budget",
        "yes",
        "no",
        "第一種",
        "第一种",
        "第二種",
        "第二种",
    )
    _INSURANCE_DOMAIN_KEYWORDS = {
        "dental": ("dental", "牙科"),
        "health_medical": ("health insurance", "medical", "hospital", "醫療", "医疗", "住院"),
        "critical_illness": ("critical illness", "ci", "危疾", "重疾"),
        "life_protection": ("life protection", "life insurance", "term life", "whole life", "人壽", "人寿", "壽險", "寿险"),
        "savings_retirement": ("savings", "retirement", "annuity", "deferred annuity", "儲蓄", "储蓄", "退休", "年金"),
        "general_protection_non_life": ("non-life", "non life", "general protection", "accident", "liability", "helper", "maid", "golf", "意外", "責任", "责任", "外傭", "外佣", "高爾夫"),
    }
    _INSURANCE_DOMAIN_REQUIRED_FACTS = {
        "dental": ("age", "residence_location", "coverage_context"),
        "health_medical": ("age", "health_conditions", "residence_location"),
        "critical_illness": ("age", "health_conditions", "desired_coverage_amount"),
        "life_protection": ("age", "health_conditions", "family_structure", "income_role", "desired_payout", "beneficiaries"),
        "savings_retirement": ("location_of_funds", "investment_amount", "wealth_goals", "growth_expectations"),
        "general_protection_non_life": ("subtype", "asset_details", "asset_usage", "asset_location"),
    }
    _INSURANCE_DOMAIN_ACTIVATION_REQUIRED_FACTS = {
        "general_protection_non_life": {"subtype"},
    }

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        max_iterations: int = 40,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        memory_window: int = 100,
        reasoning_effort: str | None = None,
        brave_api_key: str | None = None,
        web_proxy: str | None = None,
        exec_config: ExecToolConfig | None = None,
        cron_service: CronService | None = None,
        restrict_to_workspace: bool = False,
        session_manager: SessionManager | None = None,
        mcp_servers: dict | None = None,
        channels_config: ChannelsConfig | None = None,
        privacy_config: PrivacyGatewayConfig | None = None,
    ):
        from nanobot.config.schema import ExecToolConfig, PrivacyGatewayConfig
        self.bus = bus
        self.channels_config = channels_config
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.memory_window = memory_window
        self.reasoning_effort = reasoning_effort
        self.brave_api_key = brave_api_key
        self.web_proxy = web_proxy
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.restrict_to_workspace = restrict_to_workspace

        self._known_names = load_known_names(workspace)
        # Legacy fallback context (no client scoping) for non-WhatsApp sessions.
        self.context = ContextBuilder(workspace, known_names=self._known_names)
        self.sessions = session_manager or SessionManager(workspace)
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            reasoning_effort=reasoning_effort,
            brave_api_key=brave_api_key,
            web_proxy=web_proxy,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._consolidating: set[str] = set()  # Session keys with consolidation in progress
        self._consolidation_tasks: set[asyncio.Task] = set()  # Strong refs to in-flight tasks
        self._consolidation_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        self._processing_lock = asyncio.Lock()
        self._project_root = Path(__file__).resolve().parents[2]
        self._test_words_dir = self._project_root / "test_words"
        self._test_counter_file = self._test_words_dir / ".counter"
        # Privacy pipeline companion path:
        # AgentLoop writes local raw/sanitized snapshots for inspection using
        # the same deterministic sanitizer that protects cloud-bound payloads.
        self._privacy_sanitizer = TextPrivacySanitizer(
            privacy_config or PrivacyGatewayConfig(),
            known_names=load_known_names(workspace),
        )
        self._ensure_test_words_dir()
        self._register_default_tools()

    def _context_for_session(self, session_key: str) -> ContextBuilder:
        """Return a ContextBuilder scoped to the client owning *session_key*.

        For WhatsApp sessions the builder carries a per-client
        :class:`MemoryStore` so that only that client's memory (plus the
        optional global knowledge file) is injected into the prompt.

        Non-WhatsApp sessions fall back to the unscoped builder.
        """
        from nanobot.session.client_key import ClientKey
        try:
            client_key = ClientKey.from_session_key(session_key)
        except ValueError:
            return self.context  # fallback for CLI / non-WA sessions
        return ContextBuilder(self.workspace, client_key=client_key, known_names=self._known_names)

    def _register_default_tools(self) -> None:
        """Register the default set of tools."""
        allowed_dir = self.workspace if self.restrict_to_workspace else None
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(ExecTool(
            working_dir=str(self.workspace),
            timeout=self.exec_config.timeout,
            restrict_to_workspace=self.restrict_to_workspace,
            path_append=self.exec_config.path_append,
        ))
        self.tools.register(WebSearchTool(api_key=self.brave_api_key, proxy=self.web_proxy))
        self.tools.register(WebFetchTool(proxy=self.web_proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(CronTool(self.cron_service))

    def _ensure_test_words_dir(self) -> None:
        """Create the test_words folder at repo root if needed."""
        self._test_words_dir.mkdir(parents=True, exist_ok=True)
        if not self._test_counter_file.exists():
            self._test_counter_file.write_text("0\n", encoding="utf-8")

    def _next_test_file_paths(self, *, client_tag: str = "") -> tuple[Path, Path]:
        """Return the next sequential raw+sanitized test_words file paths.

        When *client_tag* is provided the phone is embedded in the filename
        so that debug artefacts are visibly scoped to one client.
        """
        try:
            raw = self._test_counter_file.read_text(encoding="utf-8").strip()
            current = int(raw) if raw else 0
        except (OSError, ValueError):
            current = 0
        next_index = current + 1
        self._test_counter_file.write_text(f"{next_index}\n", encoding="utf-8")
        tag = f"_{client_tag}" if client_tag else ""
        raw_path = self._test_words_dir / f"test_{next_index:05d}{tag}.txt"
        sanitized_path = self._test_words_dir / f"test_{next_index:05d}{tag}_sanitized.txt"
        return raw_path, sanitized_path

    @staticmethod
    def _render_snapshot_text(
        *,
        generated_at: str,
        session_key: str,
        channel: str,
        chat_id: str,
        system_prompt: str,
        history: list[dict[str, Any]],
        memory_text: str,
        history_text: str,
        user_payload: str,
        sanitizer_meta: dict[str, Any] | None = None,
    ) -> str:
        """Render one debug snapshot in the established text format."""
        lines: list[str] = [
            "===TURN_INFO===",
            f"prompt_generated_at: {generated_at}",
            f"session_key: {session_key}",
            f"channel: {channel}",
            f"chat_id: {chat_id}",
            "",
            "===SYSTEM_PROMPT===",
            str(system_prompt),
            "",
            "===CHAT_HISTORY_FOR_THIS_TURN===",
        ]
        for index, item in enumerate(history, start=1):
            lines.append(f"[{index}] {item.get('role')}: {item.get('content')}")

        lines.extend(
            [
                "",
                "===MEMORY_MD===",
                memory_text,
                "",
                "===HISTORY_MD===",
                history_text,
                "",
                "===USER_PAYLOAD===",
                str(user_payload),
                "",
            ]
        )
        if sanitizer_meta is not None:
            lines.extend(
                [
                    "===SANITIZER_META===",
                    json.dumps(sanitizer_meta, ensure_ascii=False, indent=2),
                    "",
                ]
            )
        return "\n".join(lines)

    def _write_turn_snapshot(
        self,
        *,
        session_key: str,
        channel: str,
        chat_id: str,
        initial_messages: list[dict[str, Any]],
        history: list[dict[str, Any]],
    ) -> None:
        """Write per-turn prompt/memory/history snapshot for debugging."""
        if not initial_messages:
            return

        try:
            # Read per-client memory when possible; fall back to legacy global files.
            from nanobot.session.client_key import ClientKey
            client_key = ClientKey.try_normalize(
                session_key.split(":", 1)[1] if ":" in session_key else session_key
            )
            client_tag = client_key.phone if client_key else ""
            out_raw, out_sanitized = self._next_test_file_paths(client_tag=client_tag)
            generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S (%A) (%Z)")
            system_prompt = self._stringify_message_content(initial_messages[0].get("content", ""))
            user_payload = initial_messages[-1].get("content", "")
            if client_key:
                per_client_mem = client_key.memory_dir(self.workspace) / "MEMORY.md"
                per_client_hist = client_key.memory_dir(self.workspace) / "HISTORY.md"
            else:
                per_client_mem = None
                per_client_hist = None
            memory_file = per_client_mem if (per_client_mem and per_client_mem.exists()) else self.workspace / "memory" / "MEMORY.md"
            history_file = per_client_hist if (per_client_hist and per_client_hist.exists()) else self.workspace / "memory" / "HISTORY.md"
            memory_text = memory_file.read_text(encoding="utf-8") if memory_file.exists() else ""
            history_text = history_file.read_text(encoding="utf-8") if history_file.exists() else ""

            raw_text = self._render_snapshot_text(
                generated_at=generated_at,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                system_prompt=system_prompt,
                history=history,
                memory_text=memory_text,
                history_text=history_text,
                user_payload=str(user_payload),
            )
            # Local-only artifact: raw snapshot before privacy masking.
            out_raw.write_text(raw_text, encoding="utf-8")

            # Privacy pipeline debug companion:
            # sanitize the same turn snapshot so test_words/ can show a raw vs
            # sanitized comparison using the identical masking rules.
            sanitized_result = self._privacy_sanitizer.sanitize_chat_payload(
                {"messages": initial_messages},
                headers={"x-session-affinity": session_key},
            )
            sanitized_messages = sanitized_result.sanitized_payload.get("messages", [])
            sanitized_system = self._stringify_message_content(sanitized_messages[0].get("content", "")) if sanitized_messages else ""
            sanitized_user_payload = str(sanitized_messages[-1].get("content", "")) if sanitized_messages else ""
            sanitized_history: list[dict[str, Any]] = []
            for item in sanitized_messages[1:-1]:
                if isinstance(item, dict):
                    sanitized_history.append(
                        {
                            "role": item.get("role", "unknown"),
                            "content": item.get("content"),
                        }
                    )

            sanitized_memory, _, _ = self._privacy_sanitizer.redact_text_for_debug(memory_text, session_key=session_key)
            sanitized_history_md, _, _ = self._privacy_sanitizer.redact_text_for_debug(history_text, session_key=session_key)
            sanitized_text = self._render_snapshot_text(
                generated_at=generated_at,
                session_key=session_key,
                channel=channel,
                chat_id=chat_id,
                system_prompt=sanitized_system,
                history=sanitized_history,
                memory_text=sanitized_memory,
                history_text=sanitized_history_md,
                user_payload=sanitized_user_payload,
                sanitizer_meta={
                    "blocked": sanitized_result.blocked,
                    "reasons": sanitized_result.reasons,
                    "placeholder_map": sanitized_result.placeholder_map,
                },
            )
            # Local-only artifact: sanitized snapshot with placeholder metadata.
            out_sanitized.write_text(sanitized_text, encoding="utf-8")
        except Exception:
            logger.exception("Failed to write test_words snapshot")

    @staticmethod
    def _stringify_message_content(content: Any) -> str:
        """Convert message content to readable full text for debug snapshots."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            return "\n".join(parts)
        if isinstance(content, dict):
            return json.dumps(content, ensure_ascii=False, indent=2)
        return str(content)

    async def _apply_whatsapp_self_routing_from_message(self, content: str) -> None:
        """Apply self-chat routing commands to WhatsApp local allowlists."""
        if self.channels_config is None:
            return
        wa_cfg = getattr(self.channels_config, "whatsapp", None)
        if wa_cfg is None:
            return

        from nanobot.channels.whatsapp_self_control import (
            apply_self_routing_instruction,
            parse_self_routing_instruction,
        )
        from nanobot.channels.whatsapp_reply_targets import (
            reply_targets_path,
            rewrite_from_self_instruction,
        )
        from nanobot.channels.whatsapp_contacts import normalize_contact_id

        instruction = parse_self_routing_instruction(content)
        if instruction is None:
            return

        try:
            stats = apply_self_routing_instruction(
                contacts_file=wa_cfg.contacts_file,
                group_members_file=wa_cfg.group_members_file,
                instruction=instruction,
            )
            targets_file = reply_targets_path(wa_cfg.reply_targets_file, self._project_root)
            target_stats = rewrite_from_self_instruction(
                targets_file,
                individuals=instruction.individuals,
                groups=instruction.groups,
            )
            logger.info(
                "Updated WhatsApp routing from self-chat command: contacts_cache={}, group_cache={}, direct_json={}, group_json={}",
                stats.get("individual_count", -1),
                stats.get("group_member_count", -1),
                target_stats.get("direct_reply_target_count", -1),
                target_stats.get("group_reply_target_count", -1),
            )
            if instruction.individuals is not None:
                # Reply-target updates are passive until the next login direct history parse
                # or an explicit manual sync from the UI.
                pass
        except Exception:
            logger.exception("Failed to apply WhatsApp self-chat routing command")

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy)."""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from nanobot.agent.tools.mcp import connect_mcp_servers
        try:
            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """Update context for all tools that need routing info."""
        for name in ("message", "spawn", "cron"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @classmethod
    def _default_insurance_state(cls) -> dict[str, Any]:
        return {
            cls._INSURANCE_FLOW_MODE_KEY: "generic",
            cls._INSURANCE_GENERIC_REPLY_COUNT_KEY: 0,
            cls._INSURANCE_CYCLE_ACTIVE_KEY: False,
            cls._INSURANCE_WAITING_FOR_ANSWER_KEY: False,
        }

    @classmethod
    def _get_insurance_state(cls, session: Session) -> dict[str, Any]:
        """Return normalized insurance flow state from session metadata."""
        current = cls._default_insurance_state()
        meta = session.metadata or {}

        mode = meta.get(cls._INSURANCE_FLOW_MODE_KEY)
        if mode in {"generic", "skill"}:
            current[cls._INSURANCE_FLOW_MODE_KEY] = mode

        try:
            count = int(meta.get(cls._INSURANCE_GENERIC_REPLY_COUNT_KEY, 0))
        except (TypeError, ValueError):
            count = 0
        current[cls._INSURANCE_GENERIC_REPLY_COUNT_KEY] = max(0, count)
        current[cls._INSURANCE_CYCLE_ACTIVE_KEY] = bool(meta.get(cls._INSURANCE_CYCLE_ACTIVE_KEY, False))
        current[cls._INSURANCE_WAITING_FOR_ANSWER_KEY] = bool(meta.get(cls._INSURANCE_WAITING_FOR_ANSWER_KEY, False))
        return current

    @classmethod
    def _write_insurance_state(cls, session: Session, state: dict[str, Any]) -> None:
        """Persist insurance flow state into session metadata."""
        session.metadata[cls._INSURANCE_FLOW_MODE_KEY] = state[cls._INSURANCE_FLOW_MODE_KEY]
        session.metadata[cls._INSURANCE_GENERIC_REPLY_COUNT_KEY] = state[cls._INSURANCE_GENERIC_REPLY_COUNT_KEY]
        session.metadata[cls._INSURANCE_CYCLE_ACTIVE_KEY] = state[cls._INSURANCE_CYCLE_ACTIVE_KEY]
        session.metadata[cls._INSURANCE_WAITING_FOR_ANSWER_KEY] = state[cls._INSURANCE_WAITING_FOR_ANSWER_KEY]

    @classmethod
    def _insurance_runtime_metadata(cls, state: dict[str, Any]) -> dict[str, Any]:
        """Expose the current insurance flow state to runtime context."""
        return {
            cls._INSURANCE_FLOW_MODE_KEY: state[cls._INSURANCE_FLOW_MODE_KEY],
            cls._INSURANCE_GENERIC_REPLY_COUNT_KEY: state[cls._INSURANCE_GENERIC_REPLY_COUNT_KEY],
            cls._INSURANCE_CYCLE_ACTIVE_KEY: state[cls._INSURANCE_CYCLE_ACTIVE_KEY],
        }

    @classmethod
    def _offline_meeting_runtime_metadata(cls, session: Session) -> dict[str, Any]:
        """Expose recent offline-meeting transcripts to the matching client prompt only."""
        notes = [
            str(note.get("transcript") or "").strip()
            for note in session.offline_meeting_notes
            if isinstance(note, dict) and str(note.get("transcript") or "").strip()
        ]
        if not notes:
            return {}

        return {
            cls._OFFLINE_MEETING_RUNTIME_KEY: notes[-cls._OFFLINE_MEETING_CONTEXT_LIMIT:],
        }

    @staticmethod
    def _looks_like_question(text: str | None) -> bool:
        if not text:
            return False
        lowered = text.casefold()
        return any(
            marker in text or marker in lowered
            for marker in ("?", "？", "想問", "方便講", "可唔可以", "可以講下", "which", "what", "how")
        )

    @classmethod
    def _looks_insurance_related(cls, text: str | None) -> bool:
        lowered = (text or "").casefold()
        if not lowered.strip():
            return False
        return any(keyword.casefold() in lowered for keyword in cls._INSURANCE_TOPIC_KEYWORDS)

    @classmethod
    def _looks_like_insurance_followup_answer(cls, text: str | None) -> bool:
        lowered = (text or "").casefold().strip()
        if not lowered:
            return False
        if any(keyword.casefold() in lowered for keyword in cls._INSURANCE_FOLLOWUP_KEYWORDS):
            return True
        if re.search(r"\d", lowered):
            return True
        return bool(re.fullmatch(r"(yes|no|係|是|好|可以|得|ok|okay|第一種|第一种|第二種|第二种)", lowered))

    @classmethod
    def _recent_insurance_context(cls, session: Session, max_messages: int = 6) -> bool:
        for message in reversed(session.messages[-max_messages:]):
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text"
                )
            if isinstance(content, str) and cls._looks_insurance_related(content):
                return True
        return False

    @staticmethod
    def _session_text_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return ""

    @classmethod
    def _recent_user_text(cls, session: Session, max_messages: int = 6) -> str:
        parts: list[str] = []
        for message in session.messages[-max_messages:]:
            if model_role_for_session(session.key, str(message.get("role", "") or "")) != "user":
                continue
            text = cls._session_text_content(message.get("content"))
            if text.strip():
                parts.append(text)
        return "\n".join(parts)

    @classmethod
    def _detect_insurance_domain(cls, text: str) -> str | None:
        lowered = text.casefold()
        best_domain: str | None = None
        best_score = 0
        for domain, keywords in cls._INSURANCE_DOMAIN_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword.casefold() in lowered)
            if score > best_score:
                best_domain = domain
                best_score = score
        return best_domain

    @classmethod
    def _extract_fact_signals(cls, text: str) -> set[str]:
        lowered = text.casefold()
        signals: set[str] = set()

        if re.search(r"(?:(?:age|aged)\s*\d{1,3})|(?:\d{1,3}\s*(?:歲|岁|yo|yrs?|years? old))", lowered):
            signals.add("age")

        if (
            any(token in lowered for token in ("hong kong", "香港", "macau", "macao", "澳門", "澳门", "china", "中國", "中国"))
            or re.search(r"\bhk\b", lowered)
        ):
            signals.update({"residence_location", "asset_location", "location_of_funds"})

        if any(token in lowered for token in ("individual", "personal", "employee", "employer", "company", "group", "staff", "個人", "个人", "公司", "團體", "团体", "僱員", "雇员")):
            signals.add("coverage_context")

        if any(token in lowered for token in ("healthy", "health condition", "medical history", "health history", "smoker", "diabetes", "cancer", "bp", "高血壓", "高血压", "糖尿", "病歷", "病历", "健康狀況", "健康状况", "身體健康", "身体健康")):
            signals.add("health_conditions")

        if re.search(r"(?:hk\$|usd|rmb|cny|¥|￥|萬|万|million|m\b|sum assured|coverage amount|保障額|保障额|賠償額|赔偿额|保額|保额|投資額|投资额)", lowered):
            signals.update({"desired_coverage_amount", "desired_payout", "investment_amount"})

        if any(token in lowered for token in ("married", "single", "wife", "husband", "spouse", "kid", "kids", "child", "children", "parents", "family", "已婚", "單身", "单身", "配偶", "小朋友", "子女", "父母", "家庭")):
            signals.add("family_structure")

        if any(token in lowered for token in ("breadwinner", "main income", "sole income", "income role", "收入支柱", "經濟支柱", "经济支柱", "養家", "养家")):
            signals.add("income_role")

        if any(token in lowered for token in ("beneficiary", "beneficiaries", "受益人", "配偶", "子女", "父母")):
            signals.add("beneficiaries")

        if any(token in lowered for token in ("retirement", "education fund", "legacy", "wealth growth", "asset transfer", "退休", "教育金", "傳承", "传承", "增值")):
            signals.add("wealth_goals")

        if any(token in lowered for token in ("conservative", "balanced", "aggressive", "growth", "穩健", "稳健", "平衡", "進取", "进取", "增長", "增长")):
            signals.add("growth_expectations")

        if any(token in lowered for token in ("accident", "liability", "helper", "maid", "domestic worker", "golf", "意外", "責任", "责任", "外傭", "外佣", "高爾夫")):
            signals.add("subtype")

        if any(token in lowered for token in ("property", "home", "flat", "car", "asset", "house", "物業", "物业", "家居", "住宅", "車", "车", "資產", "资产")):
            signals.add("asset_details")

        if any(token in lowered for token in ("self-use", "own use", "rental", "rent out", "work use", "family use", "自住", "出租", "工作用途", "家庭用途")):
            signals.add("asset_usage")

        return signals

    @classmethod
    def _should_force_skill_mode(cls, msg: InboundMessage, session: Session) -> bool:
        recent_text = cls._recent_user_text(session)
        combined = "\n".join(part for part in (recent_text, msg.content) if part).strip()
        if not combined:
            return False

        domain = cls._detect_insurance_domain(combined)
        if not domain:
            return False

        available_signals = cls._extract_fact_signals(combined)
        domain_signals = {
            field
            for field in cls._INSURANCE_DOMAIN_REQUIRED_FACTS.get(domain, ())
            if field in available_signals
        }

        required_signals = cls._INSURANCE_DOMAIN_ACTIVATION_REQUIRED_FACTS.get(domain, set())
        if required_signals and not required_signals.issubset(domain_signals):
            return False

        return len(domain_signals) >= 2

    @classmethod
    def _is_whatsapp_insurance_turn(cls, msg: InboundMessage, session: Session, state: dict[str, Any]) -> bool:
        if msg.channel != "whatsapp":
            return False
        if state[cls._INSURANCE_FLOW_MODE_KEY] == "skill" and state[cls._INSURANCE_CYCLE_ACTIVE_KEY]:
            if cls._looks_insurance_related(msg.content):
                return True
            if state[cls._INSURANCE_WAITING_FOR_ANSWER_KEY] and cls._looks_like_insurance_followup_answer(msg.content):
                return True
            return cls._recent_insurance_context(session) and cls._looks_like_insurance_followup_answer(msg.content)
        if cls._looks_insurance_related(msg.content):
            return True
        if state[cls._INSURANCE_CYCLE_ACTIVE_KEY] and state[cls._INSURANCE_WAITING_FOR_ANSWER_KEY]:
            if cls._looks_like_insurance_followup_answer(msg.content):
                return True
            return False
        if cls._looks_like_insurance_followup_answer(msg.content):
            return cls._recent_insurance_context(session)
        if state[cls._INSURANCE_FLOW_MODE_KEY] == "skill" and state[cls._INSURANCE_CYCLE_ACTIVE_KEY]:
            return True
        return False

    @staticmethod
    def _extract_json_payload(text: str | None) -> Any | None:
        raw = (text or "").strip()
        if not raw:
            return None
        candidates = [raw]
        for open_char, close_char in (("{", "}"), ("[", "]")):
            start = raw.find(open_char)
            end = raw.rfind(close_char)
            if start != -1 and end != -1 and end > start:
                candidates.append(raw[start:end + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    @classmethod
    def _inspect_insurance_tool_activity(cls, turn_messages: list[dict[str, Any]]) -> dict[str, Any]:
        """Inspect this turn's tool calls/results for product-flow completion."""
        pending_exec: dict[str, str] = {}
        find_result: dict[str, Any] | None = None
        research_result: dict[str, Any] | None = None

        for message in turn_messages:
            if message.get("role") == "assistant":
                for tool_call in message.get("tool_calls", []) or []:
                    function = tool_call.get("function", {}) or {}
                    if function.get("name") != "exec":
                        continue
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    command = str(arguments.get("command", ""))
                    if "find_products.py" in command:
                        pending_exec[tool_call.get("id", "")] = "find_products"
                    elif "research_products.py" in command:
                        pending_exec[tool_call.get("id", "")] = "research_products"
            elif message.get("role") == "tool":
                tool_id = str(message.get("tool_call_id", ""))
                action = pending_exec.get(tool_id)
                payload = cls._extract_json_payload(message.get("content"))
                if action == "find_products" and isinstance(payload, dict):
                    find_result = payload
                elif action == "research_products" and isinstance(payload, dict):
                    research_result = payload

        missing_fields = []
        no_fit_completed = False
        catalog_unavailable = False
        if isinstance(find_result, dict):
            missing_fields = find_result.get("missing_fields") or []
            catalog_unavailable = bool(find_result.get("catalog_unavailable"))
            candidates = find_result.get("candidates") or []
            no_fit_completed = not catalog_unavailable and not missing_fields and not candidates

        research_completed = isinstance(research_result, dict) and "candidates" in research_result
        return {
            "find_products_used": find_result is not None,
            "research_products_used": research_result is not None,
            "missing_fields": missing_fields,
            "catalog_unavailable": catalog_unavailable,
            "no_fit_completed": no_fit_completed,
            "research_completed": research_completed,
        }

    def _advance_insurance_state_after_turn(
        self,
        session: Session,
        state: dict[str, Any],
        insurance_turn: bool,
        final_content: str | None,
        turn_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Update the session insurance flow state after one completed turn."""
        next_state = dict(state)

        if not insurance_turn and not next_state[self._INSURANCE_CYCLE_ACTIVE_KEY]:
            return next_state

        if next_state[self._INSURANCE_FLOW_MODE_KEY] == "generic":
            if insurance_turn and final_content:
                next_state[self._INSURANCE_CYCLE_ACTIVE_KEY] = True
                next_state[self._INSURANCE_GENERIC_REPLY_COUNT_KEY] += 1
                next_state[self._INSURANCE_WAITING_FOR_ANSWER_KEY] = True
                if next_state[self._INSURANCE_GENERIC_REPLY_COUNT_KEY] >= self._INSURANCE_GENERIC_LIMIT:
                    next_state[self._INSURANCE_FLOW_MODE_KEY] = "skill"
            return next_state

        activity = self._inspect_insurance_tool_activity(turn_messages)
        if activity["research_completed"] or activity["no_fit_completed"]:
            return self._default_insurance_state()

        next_state[self._INSURANCE_FLOW_MODE_KEY] = "skill"
        next_state[self._INSURANCE_CYCLE_ACTIVE_KEY] = True
        next_state[self._INSURANCE_WAITING_FOR_ANSWER_KEY] = bool(
            activity["missing_fields"] or self._looks_like_question(final_content)
        )
        return next_state

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed in content."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """Format tool calls as concise hint, e.g. 'web_search("query")'."""
        def _fmt(tc):
            args = (tc.arguments[0] if isinstance(tc.arguments, list) else tc.arguments) or {}
            val = next(iter(args.values()), None) if isinstance(args, dict) else None
            if not isinstance(val, str):
                return tc.name
            return f'{tc.name}("{val[:40]}…")' if len(val) > 40 else f'{tc.name}("{val}")'
        return ", ".join(_fmt(tc) for tc in tool_calls)

    async def _run_agent_loop(
        self,
        initial_messages: list[dict],
        on_progress: Callable[..., Awaitable[None]] | None = None,
    ) -> tuple[str | None, list[str], list[dict]]:
        """Run the agent iteration loop. Returns (final_content, tools_used, messages)."""
        messages = initial_messages
        iteration = 0
        final_content = None
        tools_used: list[str] = []

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
                model=self.model,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                reasoning_effort=self.reasoning_effort,
            )

            if response.has_tool_calls:
                if on_progress:
                    thought = self._strip_think(response.content)
                    if thought:
                        await on_progress(thought)
                    await on_progress(self._tool_hint(response.tool_calls), tool_hint=True)

                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                        }
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages, response.content, tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

                for tool_call in response.tool_calls:
                    tools_used.append(tool_call.name)
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info("Tool call: {}({})", tool_call.name, args_str[:200])
                    result = await self.tools.execute(tool_call.name, tool_call.arguments)
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
            else:
                clean = self._strip_think(response.content)
                # Don't persist error responses to session history — they can
                # poison the context and cause permanent 400 loops (#1303).
                if response.finish_reason == "error":
                    logger.error("LLM returned error: {}", (clean or "")[:200])
                    final_content = clean or "Sorry, I encountered an error calling the AI model."
                    break
                messages = self.context.add_assistant_message(
                    messages, clean, reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                final_content = clean
                break

        if final_content is None and iteration >= self.max_iterations:
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            final_content = (
                f"I reached the maximum number of tool call iterations ({self.max_iterations}) "
                "without completing the task. You can try breaking the task into smaller steps."
            )

        return final_content, tools_used, messages

    async def run(self) -> None:
        """Run the agent loop, dispatching messages as tasks to stay responsive to /stop."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")
        inbound_task = asyncio.create_task(self.bus.consume_inbound())
        history_worker = asyncio.create_task(self._history_import_worker())

        try:
            while self._running:
                done, _pending = await asyncio.wait(
                    {inbound_task},
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    continue

                if inbound_task in done:
                    msg = inbound_task.result()
                    inbound_task = asyncio.create_task(self.bus.consume_inbound())
                    if msg.content.strip().lower() == "/stop":
                        await self._handle_stop(msg)
                    else:
                        task = asyncio.create_task(self._dispatch(msg))
                        self._active_tasks.setdefault(msg.session_key, []).append(task)
                        task.add_done_callback(lambda t, k=msg.session_key: self._active_tasks.get(k, []) and self._active_tasks[k].remove(t) if t in self._active_tasks.get(k, []) else None)
        finally:
            inbound_task.cancel()
            history_worker.cancel()
            await asyncio.gather(inbound_task, history_worker, return_exceptions=True)

    async def _history_import_worker(self) -> None:
        """Consume history batches in bus order so request-scoped terminal signals stay ordered."""
        while self._running:
            batch = await self.bus.consume_history()
            await self._dispatch_history(batch)

    async def _handle_stop(self, msg: InboundMessage) -> None:
        """Cancel all active tasks and subagents for the session."""
        tasks = self._active_tasks.pop(msg.session_key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(msg.session_key)
        total = cancelled + sub_cancelled
        content = f"⏹ Stopped {total} task(s)." if total else "No active task to stop."
        await self.bus.publish_outbound(OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=content,
        ))

    async def _dispatch(self, msg: InboundMessage) -> None:
        """Process a message under the global lock."""
        async with self._processing_lock:
            try:
                response = await self._process_message(msg)
                if response is not None:
                    await self.bus.publish_outbound(response)
                elif msg.channel == "cli":
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="", metadata=msg.metadata or {},
                    ))
            except asyncio.CancelledError:
                logger.info("Task cancelled for session {}", msg.session_key)
                raise
            except Exception:
                logger.exception("Error processing message for session {}", msg.session_key)
                await self.bus.publish_outbound(OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Sorry, I encountered an error.",
                ))

    async def _dispatch_history(self, batch: InboundHistoryBatch) -> None:
        """Process a historical import batch under the global lock."""
        async with self._processing_lock:
            try:
                result = self._import_history_batch(batch)
                if result is not None:
                    await self.bus.publish_history_result(result)
            except Exception:
                logger.exception("Error importing historical batch for {}", batch.channel)

    async def close_mcp(self) -> None:
        """Close MCP connections."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    def _save_session(
        self,
        session: Session,
        *,
        change_type: str = "updated",
        metadata: dict[str, Any] | None = None,
        notify_observers: bool = False,
    ) -> None:
        """Persist a session and refresh WhatsApp-visible history exports when relevant."""
        self.sessions.save_history(
            session,
            bus=self.bus,
            change_type=change_type,
            metadata=metadata,
            notify_observers=notify_observers,
        )
        self._refresh_whatsapp_history_exports(session)

    def _refresh_whatsapp_history_exports(self, session: Session) -> None:
        """Deprecated hook retained after consolidating WhatsApp data into session bundles."""
        _ = session

    def _import_history_batch(self, batch: InboundHistoryBatch) -> HistoryImportResult | None:
        """Silently merge a historical batch into canonical session files."""
        if batch.channel != "whatsapp":
            return

        request_id = str(batch.metadata.get("request_id", "") or "").strip()
        if not batch.entries:
            if request_id:
                return HistoryImportResult(
                    channel=batch.channel,
                    matched_entries=0,
                    imported_entries=0,
                    verified_entries=0,
                    phones=[],
                    verified_phones=[],
                    metadata=dict(batch.metadata or {}),
                )
            return None

        from nanobot.channels.whatsapp_contacts import normalize_contact_id
        from nanobot.session.client_key import ClientKey, CrossClientError

        touched: dict[str, dict[str, Any]] = {}
        matched_entries = 0
        imported_entries = 0
        phones_seen: set[str] = set()
        intended_message_ids_by_phone: dict[str, set[str]] = {}
        for raw in batch.entries:
            if not isinstance(raw, dict):
                continue

            session_key = str(raw.get("session_key", "") or "").strip()
            message_id = str(raw.get("message_id", "") or "").strip()
            chat_id = str(raw.get("chat_id", "") or "").strip()
            phone = str(raw.get("phone", "") or "").strip()
            if not session_key or not message_id:
                continue

            # --- Per-client isolation guard (hardened) ---
            # Require a non-empty phone on history entries so that an
            # attacker/bug cannot bypass the guard by omitting the field.
            if not phone:
                logger.warning(
                    "Skipping history entry {} — missing phone field (session {})",
                    message_id, session_key,
                )
                continue

            # Normalise both sides to digits-only before comparing so that
            # formatting differences (+852-xxx vs 852xxx) never produce
            # false matches or false rejections.
            phone_norm = normalize_contact_id(phone)
            session_phone_raw = session_key.split(":", 1)[1] if ":" in session_key else ""
            session_phone_norm = normalize_contact_id(session_phone_raw)

            if phone_norm and session_phone_norm and phone_norm != session_phone_norm:
                logger.warning(
                    "Skipping history entry {} — phone {} does not match session {}",
                    message_id, phone, session_key,
                )
                continue

            # Belt-and-suspenders: ClientKey assertion
            try:
                entry_key = ClientKey.normalize(phone)
                session_client = ClientKey.from_session_key(session_key)
                ClientKey.assert_same_client(entry_key, session_client)
            except (ValueError, CrossClientError) as exc:
                logger.warning(
                    "Skipping history entry {} — client key mismatch: {}",
                    message_id, exc,
                )
                continue

            matched_entries += 1
            if phone_norm:
                phones_seen.add(phone_norm)
                intended_message_ids_by_phone.setdefault(phone_norm, set()).add(message_id)

            bucket = touched.setdefault(
                session_key,
                {
                    "session": self.sessions.get_or_create(session_key),
                    "imports": [],
                    "existing_ids": set(),
                    "earliest_ts": None,
                },
            )
            session: Session = bucket["session"]
            if not bucket["existing_ids"]:
                bucket["existing_ids"] = {
                    str(existing.get("message_id", "") or "").strip()
                    for existing in session.messages
                    if str(existing.get("message_id", "") or "").strip()
                }
            existing_ids: set[str] = bucket["existing_ids"]
            if message_id in existing_ids:
                continue

            timestamp_iso = self._history_timestamp_iso(raw.get("timestamp"))
            timestamp_value = self._history_sort_value(timestamp_iso)
            raw_content = str(raw.get("content", "") or "")
            normalized_reply: dict[str, Any] | None = None
            if batch.channel == "whatsapp" and not bool(raw.get("from_me", False)) and raw_content.strip():
                previous_messages = [
                    existing for existing in session.messages
                    if self._history_sort_value(existing.get("timestamp")) <= timestamp_value
                ]
                previous_messages.extend(
                    existing for existing in bucket["imports"]
                    if self._history_sort_value(existing.get("timestamp")) <= timestamp_value
                )
                normalized_reply = self.detect_imported_client_reply_block(raw_content, previous_messages)

            entry = {
                "role": storage_role_for_session(
                    session_key,
                    "assistant" if bool(raw.get("from_me", False)) else "user",
                ),
                "content": str((normalized_reply or {}).get("reply_text") or raw_content),
                "timestamp": timestamp_iso,
                "message_id": message_id,
                "chat_id": chat_id,
                "sender_id": phone if not bool(raw.get("from_me", False)) else "me",
                "sender": str(raw.get("sender", "") or ""),
                "sender_phone": phone,
                "push_name": str(raw.get("push_name", "") or ""),
                "historical_import": True,
                "from_me": bool(raw.get("from_me", False)),
            }
            if normalized_reply is not None:
                entry["message_type"] = normalized_reply["message_type"]
                entry["reply_text"] = normalized_reply["reply_text"]
                entry["quoted_text"] = normalized_reply["quoted_text"]
                quoted_message_id = str(normalized_reply.get("quoted_message_id", "") or "").strip()
                if quoted_message_id:
                    entry["quoted_message_id"] = quoted_message_id
            bucket["imports"].append(entry)
            imported_entries += 1
            existing_ids.add(message_id)

            entry_dt = self._history_sort_value(timestamp_iso)
            earliest = bucket["earliest_ts"]
            if earliest is None or entry_dt < earliest:
                bucket["earliest_ts"] = entry_dt

        for session_key, payload in touched.items():
            imports = payload["imports"]
            if not imports:
                continue

            session: Session = payload["session"]
            if not session.messages and payload["earliest_ts"] is not None:
                session.created_at = payload["earliest_ts"]
            session.messages = self._merge_history_entries(session.messages, imports)
            session.updated_at = datetime.now()
            self._save_session(
                session,
                change_type="history_imported",
                metadata={
                    "request_id": request_id,
                    "imported_entries": len(imports),
                },
                notify_observers=True,
            )
            logger.info("Imported {} WhatsApp history messages into {}", len(imports), session_key)

        verified_entries = 0
        verified_phones: set[str] = set()
        for phone_norm, intended_ids in intended_message_ids_by_phone.items():
            if not intended_ids:
                continue
            session_key = f"whatsapp:{phone_norm}"
            session = touched.get(session_key, {}).get("session")
            if session is None:
                session = self.sessions.get_or_create(session_key)
            existing_ids = {
                str(existing.get("message_id", "") or "").strip()
                for existing in session.messages
                if str(existing.get("message_id", "") or "").strip()
            }
            verified_ids = intended_ids.intersection(existing_ids)
            if verified_ids:
                verified_entries += len(verified_ids)
                verified_phones.add(phone_norm)

        return HistoryImportResult(
            channel=batch.channel,
            matched_entries=matched_entries,
            imported_entries=imported_entries,
            verified_entries=verified_entries,
            phones=sorted(phones_seen),
            verified_phones=sorted(verified_phones),
            metadata=dict(batch.metadata or {}),
        )

    def _merge_history_entries(self, existing: list[dict[str, Any]], imports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Insert historical imports by timestamp without disturbing existing relative order."""
        merged = list(existing)
        ordered_imports = sorted(imports, key=lambda item: self._history_sort_value(item.get("timestamp")))
        for entry in ordered_imports:
            entry_ts = self._history_sort_value(entry.get("timestamp"))
            insert_at = len(merged)
            for index, current in enumerate(merged):
                if self._history_sort_value(current.get("timestamp")) > entry_ts:
                    insert_at = index
                    break
            merged.insert(insert_at, entry)
        return merged

    @staticmethod
    def _tokenize_imported_reply_text(text: str) -> tuple[list[str], list[tuple[int, int]]]:
        """Return non-whitespace tokens and their spans for deterministic quote matching."""
        tokens: list[str] = []
        spans: list[tuple[int, int]] = []
        for match in re.finditer(r"\S+", str(text or "")):
            tokens.append(match.group(0))
            spans.append(match.span())
        return tokens, spans

    @classmethod
    def detect_imported_client_reply_block(
        cls,
        raw_block: str,
        previous_messages: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Split imported inbound `你` quote blocks into quoted text and reply text."""
        text = str(raw_block or "")
        if not text.strip():
            return None

        lines = text.splitlines()
        first_nonempty_index: int | None = None
        for index, line in enumerate(lines):
            if line.strip():
                first_nonempty_index = index
                break
        if first_nonempty_index is None:
            return None
        if lines[first_nonempty_index].strip() != "你":
            return None

        remaining_lines = lines[first_nonempty_index + 1:]
        if not any(line.strip() for line in remaining_lines):
            return None

        candidate_body = "\n".join(remaining_lines).strip()
        candidate_tokens, candidate_spans = cls._tokenize_imported_reply_text(candidate_body)
        if not candidate_tokens:
            return None

        for previous in reversed(previous_messages):
            role = str(previous.get("role", "") or "")
            if role not in {"me", "assistant"}:
                continue

            previous_text = str(previous.get("content", "") or "")
            if not previous_text.strip():
                continue

            previous_tokens, _ = cls._tokenize_imported_reply_text(previous_text)
            if not previous_tokens or len(previous_tokens) > len(candidate_tokens):
                continue

            match_start: int | None = None
            duplicate_match = False
            for offset in range(len(candidate_tokens) - len(previous_tokens) + 1):
                if candidate_tokens[offset:offset + len(previous_tokens)] != previous_tokens:
                    continue
                if match_start is not None:
                    duplicate_match = True
                    break
                match_start = offset
            if duplicate_match or match_start is None:
                continue

            start_char = candidate_spans[match_start][0]
            end_char = candidate_spans[match_start + len(previous_tokens) - 1][1]
            before = candidate_body[:start_char].strip()
            after = candidate_body[end_char:].strip()
            reply_text = "\n".join(part for part in (before, after) if part).strip()
            if not reply_text:
                continue

            payload: dict[str, Any] = {
                "message_type": "imported_client_reply_with_quote",
                "reply_text": reply_text,
                "quoted_text": previous_text,
            }
            quoted_message_id = str(previous.get("message_id", "") or "").strip()
            if quoted_message_id:
                payload["quoted_message_id"] = quoted_message_id
            return payload

        return None

    @staticmethod
    def _history_timestamp_iso(value: Any) -> str:
        """Normalize a historical timestamp into the session JSONL format."""
        if isinstance(value, str):
            text = value.strip()
            if text:
                try:
                    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        parsed = parsed.astimezone().replace(tzinfo=None)
                    return parsed.isoformat()
                except ValueError:
                    try:
                        return datetime.fromtimestamp(AgentLoop._history_epoch_seconds(float(text))).isoformat()
                    except ValueError:
                        return text
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return datetime.now().isoformat()
        return datetime.fromtimestamp(AgentLoop._history_epoch_seconds(numeric)).isoformat()

    @staticmethod
    def _history_epoch_seconds(value: float) -> float:
        """Normalize unix timestamps in seconds, milliseconds, or finer units down to seconds."""
        numeric = float(value)
        while abs(numeric) >= 1e11:
            numeric /= 1000.0
        return numeric

    @staticmethod
    def _history_sort_value(value: Any) -> datetime:
        """Parse timestamps for stable historical insertion ordering."""
        if isinstance(value, datetime):
            return value.astimezone().replace(tzinfo=None) if value.tzinfo is not None else value
        text = str(value or "").strip()
        if text:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo is not None else parsed
            except ValueError:
                try:
                    return datetime.fromtimestamp(AgentLoop._history_epoch_seconds(float(text)))
                except ValueError:
                    pass
        return datetime.max

    @staticmethod
    def _use_full_whatsapp_history_for_prompt(channel: str, session_key: str) -> bool:
        """WhatsApp prompts use the full stored session history, not just the recent window."""
        return channel == "whatsapp" and str(session_key or "").startswith("whatsapp:")

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        persist_history: bool = True,
    ) -> OutboundMessage | None:
        """Process a single inbound message and return the response."""
        # System messages: parse origin from chat_id ("channel:chat_id")
        if msg.channel == "system":
            channel, chat_id = (msg.chat_id.split(":", 1) if ":" in msg.chat_id
                                else ("cli", msg.chat_id))
            logger.info("Processing system message from {}", msg.sender_id)
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(
                max_messages=None if self._use_full_whatsapp_history_for_prompt(channel, key) else self.memory_window,
                include_consolidated=self._use_full_whatsapp_history_for_prompt(channel, key),
            )
            messages = self._context_for_session(key).build_messages(
                history=history,
                current_message=msg.content, channel=channel, chat_id=chat_id, metadata=msg.metadata,
            )
            final_content, _, all_msgs = await self._run_agent_loop(messages)
            self._save_turn(session, all_msgs, 1 + len(history))
            self._save_session(session)
            return OutboundMessage(channel=channel, chat_id=chat_id,
                                  content=final_content or "Background task completed.")

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        key = session_key or msg.session_key
        source_session = self.sessions.get_or_create(key) if persist_history else self.sessions.read_persisted(key)
        session = source_session
        if not persist_history:
            session = Session(
                key=source_session.key,
                messages=copy.deepcopy(source_session.messages),
                created_at=source_session.created_at,
                updated_at=source_session.updated_at,
                metadata=copy.deepcopy(source_session.metadata),
                last_consolidated=source_session.last_consolidated,
            )

        if bool(msg.metadata.get("capture_only")):
            if msg.metadata.get("event_type") == "message_deleted":
                matched = session.mark_message_deleted(
                    message_id=str(msg.metadata.get("deleted_message_id") or ""),
                    deleted_by_sender=bool(msg.metadata.get("deleted_by_sender", True)),
                    deleted_at=str(msg.metadata.get("deleted_at") or ""),
                    deleter_id=str(msg.metadata.get("sender") or msg.sender_id or ""),
                    chat_id=msg.chat_id,
                )
                if persist_history:
                    self._save_session(
                        session,
                        change_type="deleted",
                        metadata={
                            "message_id": str(msg.metadata.get("deleted_message_id") or ""),
                        },
                        notify_observers=msg.channel == "whatsapp",
                    )
                logger.info(
                    "Recorded deleted WhatsApp message {} for {}:{} (matched={})",
                    msg.metadata.get("deleted_message_id"),
                    msg.channel,
                    msg.sender_id,
                    matched,
                )
                return None
            if msg.channel == "whatsapp" and bool(msg.metadata.get("is_self_chat")):
                await self._apply_whatsapp_self_routing_from_message(msg.content)
            capture_role = "assistant" if msg.channel == "whatsapp" and bool(msg.metadata.get("is_self_chat")) else "user"
            session.add_message(
                role=capture_role,
                content=msg.content,
                sender_id=msg.sender_id,
                chat_id=msg.chat_id,
                message_id=msg.metadata.get("message_id"),
                sender=str(msg.metadata.get("sender") or msg.chat_id or ""),
                sender_phone=str(msg.metadata.get("sender_phone") or msg.metadata.get("pn") or ""),
                sender_name=str(msg.metadata.get("sender_name") or msg.metadata.get("push_name") or ""),
                push_name=str(msg.metadata.get("push_name") or msg.metadata.get("sender_name") or ""),
                reply_target_label=str(msg.metadata.get("reply_target_label") or ""),
                reply_target_push_name=str(msg.metadata.get("reply_target_push_name") or ""),
                from_me=bool(msg.metadata.get("is_self_chat")),
            )
            if persist_history:
                self._save_session(
                    session,
                    change_type="message_saved",
                    metadata={
                        "message_id": str(msg.metadata.get("message_id") or ""),
                    },
                    notify_observers=msg.channel == "whatsapp",
                )
            logger.info("Captured message without reply for {}:{} (capture_only)", msg.channel, msg.sender_id)
            return None

        insurance_state = self._get_insurance_state(session) if msg.channel == "whatsapp" else None
        insurance_turn = False
        runtime_metadata = dict(msg.metadata or {})
        skill_names: list[str] | None = None

        if msg.channel == "whatsapp":
            runtime_metadata.update(self._offline_meeting_runtime_metadata(session))

        if insurance_state is not None:
            insurance_turn = self._is_whatsapp_insurance_turn(msg, session, insurance_state)
            if insurance_turn:
                insurance_state[self._INSURANCE_CYCLE_ACTIVE_KEY] = True
                if (
                    insurance_state[self._INSURANCE_GENERIC_REPLY_COUNT_KEY] >= self._INSURANCE_GENERIC_LIMIT
                    or self._should_force_skill_mode(msg, session)
                ):
                    insurance_state[self._INSURANCE_FLOW_MODE_KEY] = "skill"
                self._write_insurance_state(session, insurance_state)
                runtime_metadata.update(self._insurance_runtime_metadata(insurance_state))
                skill_names = [self._INSURANCE_SKILL_NAME]

        # Slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())
            self._consolidating.add(session.key)
            try:
                async with lock:
                    snapshot = session.messages[session.last_consolidated:]
                    if snapshot:
                        temp = Session(key=session.key)
                        temp.messages = list(snapshot)
                        if not await self._consolidate_memory(temp, archive_all=True):
                            return OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="Memory archival failed, session not cleared. Please try again.",
                            )
            except Exception:
                logger.exception("/new archival failed for {}", session.key)
                return OutboundMessage(
                    channel=msg.channel, chat_id=msg.chat_id,
                    content="Memory archival failed, session not cleared. Please try again.",
                )
            finally:
                self._consolidating.discard(session.key)

            session.clear()
            if persist_history:
                self._save_session(session)
                self.sessions.invalidate(session.key)
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="New session started.")
        if cmd == "/help":
            return OutboundMessage(channel=msg.channel, chat_id=msg.chat_id,
                                  content="🐈 nanobot commands:\n/new — Start a new conversation\n/stop — Stop the current task\n/help — Show available commands")

        unconsolidated = len(session.messages) - session.last_consolidated
        if persist_history and (unconsolidated >= self.memory_window and session.key not in self._consolidating):
            self._consolidating.add(session.key)
            lock = self._consolidation_locks.setdefault(session.key, asyncio.Lock())

            async def _consolidate_and_unlock():
                try:
                    async with lock:
                        await self._consolidate_memory(session)
                finally:
                    self._consolidating.discard(session.key)
                    _task = asyncio.current_task()
                    if _task is not None:
                        self._consolidation_tasks.discard(_task)

            _task = asyncio.create_task(_consolidate_and_unlock())
            self._consolidation_tasks.add(_task)

        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        use_full_history = self._use_full_whatsapp_history_for_prompt(msg.channel, session.key)
        history = session.get_history(
            max_messages=None if use_full_history else self.memory_window,
            include_consolidated=use_full_history,
        )
        initial_messages = self._context_for_session(session.key).build_messages(
            history=history,
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel, chat_id=msg.chat_id, metadata=runtime_metadata, skill_names=skill_names,
        )
        self._write_turn_snapshot(
            session_key=session.key,
            channel=msg.channel,
            chat_id=msg.chat_id,
            initial_messages=initial_messages,
            history=history,
        )

        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content=content, metadata=meta,
            ))

        final_content, _, all_msgs = await self._run_agent_loop(
            initial_messages, on_progress=on_progress or _bus_progress,
        )

        if final_content is None:
            final_content = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, 1 + len(history), inbound_msg=msg)
        if insurance_state is not None:
            turn_messages = all_msgs[1 + len(history):]
            next_state = self._advance_insurance_state_after_turn(
                session,
                insurance_state,
                insurance_turn=insurance_turn,
                final_content=final_content,
                turn_messages=turn_messages,
            )
            self._write_insurance_state(session, next_state)
        if persist_history:
            self._save_session(session)

        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            return None

        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)
        return OutboundMessage(
            channel=msg.channel, chat_id=msg.chat_id, content=final_content,
            metadata=msg.metadata or {},
        )

    def _save_turn(self, session: Session, messages: list[dict], skip: int, *, inbound_msg: InboundMessage | None = None) -> None:
        """Save new-turn messages into session, truncating large tool results."""
        from datetime import datetime
        attached_inbound_user = False
        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool" and isinstance(content, str) and len(content) > self._TOOL_RESULT_MAX_CHARS:
                entry["content"] = content[:self._TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the runtime-context prefix, keep only the user text.
                    parts = content.split("\n\n", 1)
                    if len(parts) > 1 and parts[1].strip():
                        entry["content"] = parts[1]
                    else:
                        continue
                if isinstance(content, list):
                    filtered = []
                    for c in content:
                        if c.get("type") == "text" and isinstance(c.get("text"), str) and c["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                            continue  # Strip runtime context from multimodal messages
                        if (c.get("type") == "image_url"
                                and c.get("image_url", {}).get("url", "").startswith("data:image/")):
                            filtered.append({"type": "text", "text": "[image]"})
                        else:
                            filtered.append(c)
                    if not filtered:
                        continue
                    entry["content"] = filtered
                if not attached_inbound_user and inbound_msg is not None:
                    entry.setdefault("message_id", inbound_msg.metadata.get("message_id"))
                    entry.setdefault("sender_id", inbound_msg.sender_id)
                    entry.setdefault("chat_id", inbound_msg.chat_id)
                    attached_inbound_user = True
            if role is not None:
                entry["role"] = storage_role_for_session(session.key, str(role))
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    async def _consolidate_memory(self, session, archive_all: bool = False) -> bool:
        """Delegate to a per-client MemoryStore.consolidate(). Returns True on success."""
        from nanobot.session.client_key import ClientKey
        try:
            client_key = ClientKey.from_session_key(session.key)
        except ValueError:
            # Non-WhatsApp sessions (e.g. "cli:direct") — use a synthetic key
            client_key = ClientKey.try_normalize(session.key.split(":", 1)[-1]) if ":" in session.key else None
            if client_key is None:
                logger.warning("Cannot derive ClientKey for session {}, skipping consolidation", session.key)
                return True
        return await MemoryStore(self.workspace, client_key).consolidate(
            session, self.provider, self.model,
            archive_all=archive_all, memory_window=self.memory_window,
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        persist_history: bool = True,
    ) -> str:
        """Process a message directly (for CLI or cron usage)."""
        await self._connect_mcp()
        msg = InboundMessage(channel=channel, sender_id="user", chat_id=chat_id, content=content)
        response = await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            persist_history=persist_history,
        )
        return response.content if response else ""
