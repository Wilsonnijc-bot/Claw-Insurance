"""Constrained insurance catalog/research tool for customer conversations."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool


class InsuranceAdvisorTool(Tool):
    """Run only the two reviewed insurance helper programs, without a shell."""

    def __init__(self, workspace: Path, timeout: int = 120):
        self._scripts = workspace / "nanobot" / "skills" / "insurance-product-advisor" / "scripts"
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "insurance_advisor"

    @property
    def description(self) -> str:
        return (
            "Safely shortlist insurance products or research shortlisted brochures. "
            "Use action=shortlist with domain and facts first; then action=research "
            "with the returned candidates."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["shortlist", "research"]},
                "domain": {"type": "string"},
                "facts": {"type": "object"},
                "candidates": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        domain: str = "",
        facts: dict[str, Any] | None = None,
        candidates: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> str:
        if action == "shortlist":
            if not domain.strip():
                return "Error: domain is required for shortlist"
            script = self._scripts / "find_products.py"
            args = ["--domain", domain, "--facts-json", json.dumps(facts or {}, ensure_ascii=False)]
        elif action == "research":
            script = self._scripts / "research_products.py"
            args = ["--candidates-json", json.dumps(candidates or [], ensure_ascii=False)]
        else:
            return "Error: unsupported insurance advisor action"

        if not script.is_file():
            return f"Error: reviewed insurance helper is unavailable: {script.name}"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self._scripts),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return "Error: insurance advisor timed out"
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            return f"Error: insurance advisor failed: {detail[:1000]}"
        return stdout.decode("utf-8", errors="replace").strip()
