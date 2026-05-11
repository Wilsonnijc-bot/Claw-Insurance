"""Memory system for persistent agent memory.

Per-client isolation invariant:
    Each ``MemoryStore`` is scoped to exactly one client (identified by a
    ``ClientKey``).  Consolidation writes to ``memory/<phone>/MEMORY.md``
    and ``memory/<phone>/HISTORY.md``.  A separate *global* knowledge file
    at ``memory/GLOBAL.md`` is operator-curated and never auto-populated
    from client conversations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.session.client_key import ClientKey
from nanobot.utils.helpers import ensure_dir

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session

# Path to the operator-curated global knowledge base (never auto-written).
_GLOBAL_KNOWLEDGE_FILENAME = "GLOBAL.md"

_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph (2-5 sentences) summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


def _read_global_knowledge(workspace: Path) -> str:
    """Read the optional operator-curated global knowledge file."""
    gk = workspace / "memory" / _GLOBAL_KNOWLEDGE_FILENAME
    if gk.exists():
        return gk.read_text(encoding="utf-8")
    # Backwards-compat: if legacy memory/MEMORY.md exists but no per-client
    # dirs have been created yet, treat it as the global file.
    legacy = workspace / "memory" / "MEMORY.md"
    if legacy.exists():
        # Only treat as global if there are no per-client memory dirs yet.
        memory_root = workspace / "memory"
        has_client_dirs = any(
            p.is_dir() and p.name.isdigit()
            for p in memory_root.iterdir()
        ) if memory_root.exists() else False
        if not has_client_dirs:
            return legacy.read_text(encoding="utf-8")
    return ""


class MemoryStore:
    """Two-layer per-client memory: MEMORY.md (long-term facts) + HISTORY.md (grep-searchable log).

    Each instance is bound to a single :class:`ClientKey`.
    """

    def __init__(self, workspace: Path, client_key: ClientKey):
        self.workspace = workspace
        self.client_key = client_key
        self.memory_dir = ensure_dir(client_key.memory_dir(workspace))
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def get_memory_context(self) -> str:
        parts: list[str] = []
        long_term = self.read_long_term()
        if long_term:
            parts.append(f"## Client Memory\n{long_term}")
        global_knowledge = _read_global_knowledge(self.workspace)
        if global_knowledge:
            parts.append(f"## Global Knowledge\n{global_knowledge}")
        return "\n\n".join(parts)

    async def consolidate(
        self,
        session: Session,
        provider: LLMProvider,
        model: str,
        *,
        archive_all: bool = False,
        memory_window: int = 50,
    ) -> bool:
        """Consolidate old messages into per-client MEMORY.md + HISTORY.md via LLM tool call.

        Returns True on success (including no-op), False on failure.

        Raises :class:`CrossClientError` when the session does not belong to
        this store's client.
        """
        # --- Per-client isolation guard ---
        if session.key.startswith("whatsapp:"):
            session_client = ClientKey.from_session_key(session.key)
            ClientKey.assert_same_client(self.client_key, session_client)
        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info("Memory consolidation (archive_all): {} messages", len(session.messages))
        else:
            keep_count = memory_window // 2
            if len(session.messages) <= keep_count:
                return True
            if len(session.messages) - session.last_consolidated <= 0:
                return True
            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return True
            logger.info("Memory consolidation: {} to consolidate, {} keep", len(old_messages), keep_count)

        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}")

        current_memory = self.read_long_term()
        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{chr(10).join(lines)}"""

        try:
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation."},
                    {"role": "user", "content": prompt},
                ],
                tools=_SAVE_MEMORY_TOOL,
                model=model,
            )

            if not response.has_tool_calls:
                logger.warning("Memory consolidation: LLM did not call save_memory, skipping")
                return False

            args = response.tool_calls[0].arguments
            # Some providers return arguments as a JSON string instead of dict
            if isinstance(args, str):
                args = json.loads(args)
            # Some providers return arguments as a list (handle edge case)
            if isinstance(args, list):
                if args and isinstance(args[0], dict):
                    args = args[0]
                else:
                    logger.warning("Memory consolidation: unexpected arguments as empty or non-dict list")
                    return False
            if not isinstance(args, dict):
                logger.warning("Memory consolidation: unexpected arguments type {}", type(args).__name__)
                return False

            if entry := args.get("history_entry"):
                if not isinstance(entry, str):
                    entry = json.dumps(entry, ensure_ascii=False)
                self.append_history(entry)
            if update := args.get("memory_update"):
                if not isinstance(update, str):
                    update = json.dumps(update, ensure_ascii=False)
                if update != current_memory:
                    self.write_long_term(update)

            session.last_consolidated = 0 if archive_all else len(session.messages) - keep_count
            logger.info("Memory consolidation done: {} messages, last_consolidated={}", len(session.messages), session.last_consolidated)
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return False
