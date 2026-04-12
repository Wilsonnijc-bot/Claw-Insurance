"""Entry point for running nanobot as a module: python -m nanobot."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    """Dispatch lightweight docker-up bootstrap before importing the full CLI."""
    args = list(sys.argv[1:] if argv is None else argv)

    if args and args[0] == "docker-up":
        from nanobot.docker_up_bootstrap import main as docker_up_main

        return docker_up_main(args[1:])

    from nanobot.cli.commands import app

    if argv is None:
        app()
    else:
        app(args=args, prog_name="nanobot")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
