"""Docker-only entrypoint for the Nanobot launcher."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from nanobot.api.launcher import LauncherServer
from nanobot.config.loader import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nanobot Docker runtime")
    parser.add_argument("--api-port", type=int, default=3456)
    parser.add_argument("--config", "-c", default="")
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


async def run(api_port: int, config_path: str = "") -> None:
    config = load_config(Path(config_path) if config_path else None)
    server = LauncherServer(config=config, api_port=api_port)
    try:
        await server.start()
        await asyncio.Event().wait()
    finally:
        await server.stop()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    try:
        asyncio.run(run(api_port=args.api_port, config_path=args.config))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
