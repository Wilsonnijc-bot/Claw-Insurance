"""No public module CLI is exposed for the Docker-only Nanobot runtime."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    """Return an error because startup is managed by Docker Compose."""
    print("Nanobot is started with Docker Compose. See README.md for supported commands.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
