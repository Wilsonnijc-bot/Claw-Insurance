"""Standalone entrypoint used to package the host CDP helper with PyInstaller."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from nanobot import _non_macos_cdp_helper
from nanobot import linux_cdp_helper
from nanobot import macos_cdp_helper
from nanobot import windows_cdp_helper


def _platform_name() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system in {"windows", "linux"}:
        return system
    raise RuntimeError(f"Unsupported CDP helper platform: {platform.system()}")


def _update_env_file(path: Path, values: dict[str, str]) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    managed = set(values)
    kept = [
        line
        for line in existing
        if not any(line.startswith(f"{key}=") for key in managed)
    ]
    kept.extend(f"{key}={value}" for key, value in values.items())
    path.write_text("\n".join(kept).rstrip() + "\n", encoding="utf-8")


def _install(project_root: Path) -> dict[str, str]:
    platform_name = _platform_name()
    frozen_executable = sys.executable if getattr(sys, "frozen", False) else ""

    if platform_name == "windows":
        windows_cdp_helper.resolve_chrome_path(os.environ.get("WEB_CDP_CHROME_PATH", ""))
        token = windows_cdp_helper.load_or_create_helper_token()
        result = windows_cdp_helper.install_windows_helper(
            helper_token=token,
            frozen_executable=frozen_executable,
        )
    elif platform_name == "linux":
        linux_cdp_helper.resolve_chrome_path(os.environ.get("WEB_CDP_CHROME_PATH", ""))
        token = linux_cdp_helper.load_or_create_helper_token()
        result = linux_cdp_helper.install_linux_helper(
            helper_token=token,
            frozen_executable=frozen_executable,
        )
    else:
        macos_cdp_helper.resolve_chrome_path(os.environ.get("WEB_CDP_CHROME_PATH", ""))
        token = ""
        result = macos_cdp_helper.install_launchd_helper(frozen_executable=frozen_executable)

    project_root = project_root.resolve()
    values = {
        "WEB_CDP_URL": "http://host.docker.internal:9222",
        "WEB_CDP_HELPER_URL": "http://host.docker.internal:9230",
        "WEB_CDP_HELPER_TOKEN": token,
        "WEB_CDP_HELPER_PLATFORM": platform_name,
        "WEB_HOST_PROFILE_DIR": str(project_root / "whatsapp-web"),
        "WEB_HISTORY_SYNC_ENABLED": "true",
    }
    _update_env_file(project_root / ".env", values)
    return result


def _serve(host: str, port: int, token_file: str) -> None:
    platform_name = _platform_name()
    if platform_name == "windows":
        windows_cdp_helper.serve(host=host, port=port, token_file=token_file)
    elif platform_name == "linux":
        linux_cdp_helper.serve(host=host, port=port, token_file=token_file)
    else:
        macos_cdp_helper.serve(host=host, port=port)


def _healthy(helper_url: str) -> bool:
    if _platform_name() == "macos":
        return macos_cdp_helper.request_helper_health(helper_url)
    return _non_macos_cdp_helper.request_helper_health(helper_url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Claw Insurance host CDP helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install and start the helper")
    install.add_argument("--project-root", default=str(Path.cwd()))
    install.add_argument("--json", action="store_true")

    serve = subparsers.add_parser("serve", help="Run the helper HTTP service")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=9230)
    serve.add_argument("--token-file", default="")

    health = subparsers.add_parser("health", help="Check helper health")
    health.add_argument("--helper-url", default="http://127.0.0.1:9230")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            result = _install(Path(args.project_root))
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            else:
                print("Host CDP helper installed and started.")
            return 0
        if args.command == "serve":
            _serve(args.host, args.port, args.token_file)
            return 0
        return 0 if _healthy(args.helper_url) else 1
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
