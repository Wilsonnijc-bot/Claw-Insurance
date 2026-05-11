from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_STARTUP_TEXT = (
    "python -m nanobot",
    "python3 -m nanobot",
    "py -3 -m nanobot",
    "./docker-up",
    "./bootstrap",
    "whatsapp-web-nanobot-ui",
    "whatsapp-web-nanobot-gateway",
    "install-ui-command",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_pyproject_exposes_no_console_startup_scripts() -> None:
    pyproject = _read("pyproject.toml")

    assert "[project.scripts]" not in pyproject
    assert "nanobot.cli.commands:app" not in pyproject
    assert "whatsapp-web-nanobot" not in pyproject


def test_root_compose_uses_only_gateway_and_frontend_services() -> None:
    compose = _read("docker-compose.yml")

    assert "nanobot-gateway:" in compose
    assert "nanobot-frontend:" in compose
    assert "nanobot-cli:" not in compose
    assert "profiles:" not in compose
    assert "3456:3456" in compose
    assert "8080:80" in compose
    assert "nanobot.runtime.docker_entrypoint" in compose or "nanobot.runtime.docker_entrypoint" in _read("Dockerfile")
    assert 'command: ["launcher", "--api-port", "3456"]' not in compose


def test_runtime_modules_do_not_import_removed_cli() -> None:
    launcher = _read("nanobot/api/launcher.py")
    server = _read("nanobot/api/server.py")

    assert "nanobot.cli.commands" not in launcher
    assert "nanobot.cli.commands" not in server


def test_legacy_startup_files_are_removed() -> None:
    assert not (ROOT / "bootstrap").exists()
    assert not (ROOT / "docker-up").exists()
    assert not (ROOT / "nanobot/docker_up_bootstrap.py").exists()
    assert not (ROOT / "nanobot/cli/commands.py").exists()


def test_readme_documents_only_docker_runtime_commands() -> None:
    readme = _read("README.md")

    for command in (
        "docker compose up -d --build",
        "docker compose up -d",
        "docker compose down",
        "docker compose ps",
        "docker compose logs -f",
        "docker compose logs -f nanobot-gateway",
        "docker compose restart nanobot-gateway",
    ):
        assert command in readme

    for forbidden in FORBIDDEN_STARTUP_TEXT:
        assert forbidden not in readme


def test_runtime_status_messages_do_not_suggest_python_startup_commands() -> None:
    server = _read("nanobot/api/server.py")

    for forbidden in FORBIDDEN_STARTUP_TEXT:
        assert forbidden not in server
