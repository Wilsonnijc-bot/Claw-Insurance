from __future__ import annotations

from pathlib import Path


def test_readme_lists_platform_specific_docker_up_commands() -> None:
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")

    assert "`./docker-up` or `python3 -m nanobot docker-up`" in readme
    assert "`python3 -m nanobot docker-up`" in readme
    assert "`py -3 -m nanobot docker-up`" in readme
    assert "docker compose up -d --build" in readme
