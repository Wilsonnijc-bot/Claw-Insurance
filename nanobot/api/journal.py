from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


class ActivityJournal:
    """Append-only JSONL activity journal for backend and UI events."""

    def __init__(self, workspace_path: Path, *, relative_path: str = "state/activity_journal.jsonl", max_entries: int = 2000):
        self.path = workspace_path / relative_path
        self.max_entries = max_entries
        self._lock = asyncio.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def log(
        self,
        *,
        action: str,
        description: str,
        client_id: str | None = None,
        client_name: str | None = None,
        details: dict[str, Any] | None = None,
        user_id: str | None = None,
        user_name: str | None = None,
        source: str = "backend",
        timestamp: str | None = None,
    ) -> dict[str, Any]:
        entry = {
            "id": f"journal_{uuid4().hex}",
            "timestamp": timestamp or datetime.now().isoformat(),
            "action": action,
            "description": description,
            "clientId": client_id,
            "clientName": client_name,
            "details": details or {},
            "userId": user_id,
            "userName": user_name,
            "source": source,
        }
        await self.append(entry)
        return entry

    async def append(self, entry: dict[str, Any]) -> None:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            await self._trim_if_needed()

    async def list_entries(self, *, limit: int = 200) -> list[dict[str, Any]]:
        async with self._lock:
            if not self.path.exists():
                return []
            try:
                lines = self.path.read_text(encoding="utf-8").splitlines()
            except Exception:
                return []

        entries: list[dict[str, Any]] = []
        for line in reversed(lines):
            raw = line.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except Exception:
                continue
            if len(entries) >= limit:
                break
        return entries

    async def clear(self) -> None:
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    async def _trim_if_needed(self) -> None:
        if self.max_entries <= 0 or not self.path.exists():
            return
        lines = self.path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= self.max_entries:
            return
        self.path.write_text("\n".join(lines[-self.max_entries:]) + "\n", encoding="utf-8")
