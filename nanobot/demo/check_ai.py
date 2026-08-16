"""Send one harmless probe through the configured privacy and AI provider chain."""

from __future__ import annotations

import asyncio
import json

from nanobot.config.loader import load_config
from nanobot.runtime.gateway_runtime import (
    make_provider,
    maybe_enable_privacy_gateway,
    stop_background_process,
)


async def _check() -> int:
    config = load_config()
    privacy_process = None
    try:
        privacy_process = maybe_enable_privacy_gateway(config)
        provider = make_provider(config)
        response = await provider.chat(
            [{"role": "user", "content": "Reply with exactly: DEMO_AI_OK"}],
            model=config.agents.defaults.model,
            max_tokens=512,
            temperature=1.0,
        )
        if response.finish_reason == "error":
            detail = (response.content or "AI provider returned an unspecified error").strip()
            api_key = config.get_api_key() or ""
            if api_key:
                detail = detail.replace(api_key, "[REDACTED]")
            print(
                json.dumps(
                    {
                        "status": "error",
                        "model": config.agents.defaults.model,
                        "detail": detail[:500],
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        content = (response.content or "").strip()
        if not content:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "model": config.agents.defaults.model,
                        "detail": "provider returned no final text; increase the completion token budget",
                    },
                    ensure_ascii=False,
                )
            )
            return 1
        print(
            json.dumps(
                {
                    "status": "ok",
                    "model": config.agents.defaults.model,
                    "response": content[:200],
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        stop_background_process(privacy_process)


def main() -> int:
    return asyncio.run(_check())


if __name__ == "__main__":
    raise SystemExit(main())
