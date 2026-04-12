"""Lightweight bootstrap for Docker + host CDP startup.

This module intentionally avoids importing the full Nanobot CLI stack so
``python3 -m nanobot docker-up`` works from a plain repo checkout without
installing Typer or the rest of the package dependencies.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


def docker_up_root_dir() -> Path:
    """Return the repository root for the active checkout."""
    return Path(__file__).resolve().parents[1]


def host_helper_platform_name() -> str:
    """Return the normalized platform token used by host CDP helpers."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def non_macos_desktop_ready(platform_name: str) -> bool:
    """Return whether a non-macOS desktop session is available."""
    if platform_name != "linux":
        return True
    return any(
        str(os.environ.get(name) or "").strip()
        for name in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")
    )


def print_error(message: str) -> None:
    print(message, file=sys.stderr)


def require_command(name: str, guidance: str) -> bool:
    """Return True when the requested command exists on PATH."""
    if shutil.which(name):
        return True
    print_error(f"{name} not found. {guidance}")
    return False


def check_docker_compose_available() -> bool:
    """Return True when Docker Compose v2 is available."""
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        print_error(f"Failed to run docker compose: {exc}")
        return False

    if result.returncode == 0:
        return True

    print_error("docker compose is not available. Update Docker so Compose v2 is installed.")
    return False


def build_docker_up_env(root_dir: Path) -> dict[str, str]:
    """Return the runtime environment used for docker compose up."""
    env = {key: str(value) for key, value in os.environ.items()}
    env["WEB_BROWSER_MODE"] = str(env.get("WEB_BROWSER_MODE") or "cdp")
    env["WEB_CDP_URL"] = str(env.get("WEB_CDP_URL") or "http://host.docker.internal:9222")
    env["WEB_CDP_HELPER_URL"] = str(
        env.get("WEB_CDP_HELPER_URL") or "http://host.docker.internal:9230"
    )
    env["WEB_HOST_PROFILE_DIR"] = str(
        Path(env.get("WEB_HOST_PROFILE_DIR") or (root_dir / "whatsapp-web")).resolve()
    )
    Path(env["WEB_HOST_PROFILE_DIR"]).mkdir(parents=True, exist_ok=True)
    env.setdefault("WEB_HISTORY_SYNC_ENABLED", "true")
    return env


def run_docker_up(services: Sequence[str] | None = None) -> int:
    """Run host preflight, ensure the CDP helper, and start Docker services."""
    root_dir = docker_up_root_dir()
    platform_name = host_helper_platform_name()
    service_args = list(services or [])

    if not require_command(
        "docker",
        "Install Docker Desktop or another Docker engine with Compose support.",
    ):
        return 1
    if not check_docker_compose_available():
        return 1

    env = build_docker_up_env(root_dir)

    if platform_name == "macos":
        from nanobot.macos_cdp_helper import (
            DEFAULT_HELPER_URL,
            install_launchd_helper,
            request_helper_health,
            resolve_chrome_path,
        )

        print("Checking macOS Chrome/CDP prerequisites...")
        try:
            resolve_chrome_path(str(env.get("WEB_CDP_CHROME_PATH") or ""))
        except Exception as exc:
            print_error(str(exc))
            return 1

        env["WEB_CDP_HELPER_PLATFORM"] = "macos"
        env["WEB_CDP_HELPER_URL"] = str(env.get("WEB_CDP_HELPER_URL") or DEFAULT_HELPER_URL)
        env.pop("WEB_CDP_HELPER_TOKEN", None)

        if request_helper_health(DEFAULT_HELPER_URL, timeout_s=0.5):
            print("macOS CDP helper already healthy")
        else:
            print("Installing macOS CDP helper...")
            try:
                install_launchd_helper()
            except Exception as exc:
                print_error(f"Failed to install the macOS CDP helper: {exc}")
                return 1
    elif platform_name == "linux":
        from nanobot.linux_cdp_helper import (
            DEFAULT_HELPER_URL,
            install_linux_helper,
            load_or_create_helper_token,
            request_helper_health,
            resolve_chrome_path,
        )

        if not non_macos_desktop_ready(platform_name):
            print_error("Linux Docker sync requires a desktop session (DISPLAY or WAYLAND_DISPLAY).")
            return 1

        print("Checking Linux Chrome/CDP prerequisites...")
        try:
            resolve_chrome_path(str(env.get("WEB_CDP_CHROME_PATH") or ""))
        except Exception as exc:
            print_error(str(exc))
            return 1

        env["WEB_CDP_HELPER_PLATFORM"] = "linux"
        env["WEB_CDP_HELPER_URL"] = str(env.get("WEB_CDP_HELPER_URL") or DEFAULT_HELPER_URL)
        env["WEB_CDP_HELPER_TOKEN"] = load_or_create_helper_token()

        if request_helper_health(DEFAULT_HELPER_URL, timeout_s=0.5):
            print("Linux CDP helper already healthy")
        else:
            print("Installing Linux CDP helper...")
            try:
                install_linux_helper(helper_token=env["WEB_CDP_HELPER_TOKEN"])
            except Exception as exc:
                print_error(f"Failed to install the Linux CDP helper: {exc}")
                return 1
    elif platform_name == "windows":
        from nanobot.windows_cdp_helper import (
            DEFAULT_HELPER_URL,
            install_windows_helper,
            load_or_create_helper_token,
            request_helper_health,
            resolve_chrome_path,
        )

        print("Checking Windows Chrome/CDP prerequisites...")
        try:
            resolve_chrome_path(str(env.get("WEB_CDP_CHROME_PATH") or ""))
        except Exception as exc:
            print_error(str(exc))
            return 1

        env["WEB_CDP_HELPER_PLATFORM"] = "windows"
        env["WEB_CDP_HELPER_URL"] = str(env.get("WEB_CDP_HELPER_URL") or DEFAULT_HELPER_URL)
        env["WEB_CDP_HELPER_TOKEN"] = load_or_create_helper_token()

        if request_helper_health(DEFAULT_HELPER_URL, timeout_s=0.5):
            print("Windows CDP helper already healthy")
        else:
            print("Installing Windows CDP helper...")
            try:
                install_windows_helper(helper_token=env["WEB_CDP_HELPER_TOKEN"])
            except Exception as exc:
                print_error(f"Failed to install the Windows CDP helper: {exc}")
                return 1
    else:
        env["WEB_HISTORY_SYNC_ENABLED"] = "false"
        env["WEB_CDP_HELPER_URL"] = ""
        env.pop("WEB_CDP_HELPER_TOKEN", None)
        env["WEB_CDP_HELPER_PLATFORM"] = platform_name
        print(
            "Docker startup is supported on this host, but WhatsApp history sync is not prepared here."
        )

    print("Starting Docker services...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "--build", *service_args],
            cwd=root_dir,
            env=env,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print_error(f"docker compose up failed with exit code {exc.returncode}")
        return exc.returncode

    return 0


def print_help() -> None:
    """Print lightweight help for the docker-up bootstrap."""
    print("Usage: python3 -m nanobot docker-up [SERVICE...]")
    print("")
    print("Runs host CDP preflight, installs or reuses the host helper,")
    print("and then starts the Docker stack with:")
    print("  docker compose up -d --build [SERVICE...]")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the lightweight docker-up bootstrap."""
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "docker-up":
        args = args[1:]

    if args and args[0] in {"-h", "--help"}:
        print_help()
        return 0

    return run_docker_up(args)

