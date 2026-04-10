"""Context builder for assembling agent prompts.

Per-client isolation invariant:
    Each ``ContextBuilder`` is scoped (via its ``MemoryStore``) to
    exactly one client.  Only that client's memory and the optional
    global knowledge file are injected into the LLM prompt.
"""

import base64
import mimetypes
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.agent.memory import MemoryStore
from nanobot.agent.skills import SkillsLoader
from nanobot.session.client_key import ClientKey
from nanobot.utils.helpers import detect_image_mime


class ContextBuilder:
    """Builds the context (system prompt + messages) for the agent."""

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    BOOTSTRAP_SECTIONS = {
        "AGENTS.md": "Operational Policy",
        "SOUL.md": "Business Persona And Messaging",
        "USER.md": "Operator Profile And Preferences",
        "TOOLS.md": "Current Tool Limitations",
        "IDENTITY.md": "Additional Identity",
    }
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"

    def __init__(
        self,
        workspace: Path,
        *,
        client_key: ClientKey | None = None,
        known_names: set[str] | None = None,
    ):
        self.workspace = workspace
        self.client_key = client_key
        # Per-client memory when a ClientKey is provided; legacy global fallback otherwise.
        if client_key is not None:
            self.memory = MemoryStore(workspace, client_key)
        else:
            self.memory: MemoryStore | None = None  # type: ignore[assignment]
        self.skills = SkillsLoader(workspace)
        # Names to redact from memory content before injection into the prompt.
        self._known_names: set[str] = {n for n in (known_names or set()) if n and len(n) >= 2}

    def build_system_prompt(self, skill_names: list[str] | None = None) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity()]

        bootstrap_sections = self._load_bootstrap_files()
        if bootstrap_sections:
            parts.extend(bootstrap_sections)

        memory = self.memory.get_memory_context() if self.memory else ""
        if memory:
            # Per-client memory is already scoped — redaction of known names
            # is kept as defence-in-depth for the global knowledge block.
            for name in sorted(self._known_names, key=len, reverse=True):
                if name in memory:
                    memory = memory.replace(name, "[REDACTED_NAME]")
            parts.append(f"# Memory\n\n{memory}")

        requested_skills = [name for name in dict.fromkeys(skill_names or []) if name]
        if requested_skills:
            requested_content = self.skills.load_skills_for_context(requested_skills)
            if requested_content:
                parts.append(f"# Requested Skills\n\n{requested_content}")

        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary()
        if skills_summary:
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return f"""# Core Identity And Hard Rules

You are nanobot, the workspace's messaging assistant.

## Runtime
{runtime}

## Workspace
Your workspace is "." (current directory). All tool paths are relative to it.
- Long-term memory: memory/MEMORY.md (write important facts here)
- History log: memory/HISTORY.md (grep-searchable). Each entry starts with [YYYY-MM-DD HH:MM].
- Custom skills: skills/{{skill-name}}/SKILL.md

## Hard Rules
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel."""

    @staticmethod
    def _sanitize_runtime_value(value: Any, max_chars: int = 120) -> str:
        """Normalize a runtime metadata value so it is short and single-line."""
        text = " ".join(str(value or "").split())
        return text[:max_chars]

    @classmethod
    def _build_runtime_context(
        cls,
        channel: str | None,
        chat_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Build untrusted runtime metadata block for injection before the user message."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [f"Current Time: {now} ({tz})"]
        meta = metadata or {}
        if channel:
            lines.append(f"Channel: {channel}")
            # Omit raw Chat ID from the prompt — the message tool receives it
            # programmatically via set_context(), so the LLM never needs it.

        is_group = bool(meta.get("is_group"))
        conversation_mode = ""
        if channel == "whatsapp":
            conversation_mode = "whatsapp_group" if is_group else "whatsapp_direct"
        elif channel:
            conversation_mode = cls._sanitize_runtime_value(channel)

        if conversation_mode:
            lines.append(f"Conversation Mode: {conversation_mode}")
        if channel == "whatsapp" or "is_group" in meta:
            lines.append(f"Is Group: {'true' if is_group else 'false'}")

        if group_name := cls._sanitize_runtime_value(meta.get("group_name")):
            lines.append(f"Group Name: {group_name}")
        if sender_name := cls._sanitize_runtime_value(meta.get("sender_name") or meta.get("push_name")):
            lines.append(f"Sender Name: {sender_name}")
        # Only include Sender Phone in group chats where it disambiguates participants.
        # In DM contexts it duplicates the chat identity and leaks the number gratuitously.
        if is_group:
            if sender_phone := cls._sanitize_runtime_value(meta.get("sender_phone") or meta.get("pn")):
                lines.append(f"Sender Phone: {sender_phone}")
        if flow_mode := cls._sanitize_runtime_value(meta.get("insurance_flow_mode")):
            lines.append(f"Insurance Flow Mode: {flow_mode}")
        if "insurance_generic_reply_count" in meta:
            lines.append(f"Insurance Generic Reply Count: {meta.get('insurance_generic_reply_count', 0)}")
        if "insurance_cycle_active" in meta:
            cycle_active = "true" if bool(meta.get("insurance_cycle_active")) else "false"
            lines.append(f"Insurance Cycle Active: {cycle_active}")
        offline_notes = meta.get("offline_meeting_notes")
        if isinstance(offline_notes, list):
            for index, note in enumerate(offline_notes, start=1):
                text = cls._sanitize_runtime_value(note, max_chars=320)
                if text:
                    lines.append(f"Offline Meeting Note {index}: {text}")

        return ContextBuilder._RUNTIME_CONTEXT_TAG + "\n" + "\n".join(lines)

    def _load_bootstrap_files(self) -> list[str]:
        """Load all bootstrap files from workspace."""
        parts: list[str] = []

        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                title = self.BOOTSTRAP_SECTIONS.get(filename, filename)
                parts.append(f"# {title}\n\n## {filename}\n\n{content}")

        return parts

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call."""
        runtime_ctx = self._build_runtime_context(channel, chat_id, metadata)
        user_content = self._build_user_content(current_message, media)

        # Merge runtime context and user content into a single user message
        # to avoid consecutive same-role messages that some providers reject.
        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content

        return [
            {"role": "system", "content": self.build_system_prompt(skill_names)},
            *history,
            {"role": "user", "content": merged},
        ]

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            # Detect real MIME type from magic bytes; fallback to filename guess
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]],
        tool_call_id: str, tool_name: str, result: str,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list."""
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result})
        return messages

    def add_assistant_message(
        self, messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list."""
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if reasoning_content is not None:
            msg["reasoning_content"] = reasoning_content
        if thinking_blocks:
            msg["thinking_blocks"] = thinking_blocks
        messages.append(msg)
        return messages
