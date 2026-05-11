from __future__ import annotations

import os
import stat
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_COMMANDS = (
    "python -m nanobot",
    "python3 -m nanobot",
    "py -3 -m nanobot",
    "./docker-up",
    "./bootstrap",
    "whatsapp-web-nanobot-ui",
    "whatsapp-web-nanobot-gateway",
)


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_platform_host_cdp_installer_scripts_exist() -> None:
    macos = ROOT / "scripts" / "install-cdp-helper-macos.sh"
    linux = ROOT / "scripts" / "install-cdp-helper-linux.sh"
    windows = ROOT / "scripts" / "install-cdp-helper-windows.ps1"

    assert macos.exists()
    assert linux.exists()
    assert windows.exists()
    assert os.access(macos, os.X_OK)
    assert os.access(linux, os.X_OK)
    assert stat.S_IMODE(macos.stat().st_mode) & stat.S_IXUSR
    assert stat.S_IMODE(linux.stat().st_mode) & stat.S_IXUSR


def test_platform_host_cdp_installer_scripts_do_not_call_old_nanobot_cli() -> None:
    combined = "\n".join(
        _read(path)
        for path in (
            "scripts/install-cdp-helper-macos.sh",
            "scripts/install-cdp-helper-linux.sh",
            "scripts/install-cdp-helper-windows.ps1",
        )
    )

    for old in OLD_COMMANDS:
        assert old not in combined
    assert "nanobot.macos_cdp_helper" in combined
    assert "nanobot.linux_cdp_helper" in combined
    assert "nanobot.windows_cdp_helper" in combined
    assert "-c $Code" not in combined
    assert "Set-Content -Path $TempScript -Value $Code" in combined
    assert "if ($ExitCode -ne 0)" in combined
    assert '$env:OS -ne "Windows_NT"' in combined
    assert "& $PyLauncher.Source -3 $TempScript $RootDir" in combined


def test_readme_separates_host_prereqs_from_daily_docker_runtime() -> None:
    readme = _read("README.md")

    assert "One-Time Host Prerequisites" in readme
    assert "Daily Docker Runtime" in readme
    assert "scripts/install-cdp-helper-macos.sh" in readme
    assert "scripts/install-cdp-helper-linux.sh" in readme
    assert "install-cdp-helper-windows.ps1" in readme
    assert "Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass" in readme
    assert "powershell -ExecutionPolicy Bypass -File" not in readme
    assert "Docker Compose remains the only supported app runtime" in readme
    assert "Docker does not install host services" in readme

    for old in OLD_COMMANDS:
        assert old not in readme


def test_docker_side_cdp_messages_are_actionable_without_old_commands() -> None:
    server = _read("nanobot/api/server.py")

    assert "Host Chrome/CDP helper is not reachable." in server
    assert "Install/start the host CDP helper first, then restart Docker Compose." in server
    for old in OLD_COMMANDS:
        assert old not in server
