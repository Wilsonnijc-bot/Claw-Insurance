"""macOS helper that launches or reuses a host Chrome CDP window."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shlex
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib import parse, request

DEFAULT_HELPER_HOST = "127.0.0.1"
DEFAULT_HELPER_PORT = 9230
DEFAULT_HELPER_URL = f"http://{DEFAULT_HELPER_HOST}:{DEFAULT_HELPER_PORT}"
DEFAULT_START_URL = "https://web.whatsapp.com/"
HELPER_LABEL = "com.nanobot.macos-cdp-helper"


def helper_install_dir() -> Path:
    """Return the user-local install directory for the helper."""
    return Path.home() / "Library" / "Application Support" / "nanobot-macos-cdp-helper"


def helper_launch_agent_path() -> Path:
    """Return the per-user LaunchAgent plist path."""
    return Path.home() / "Library" / "LaunchAgents" / f"{HELPER_LABEL}.plist"


def helper_python_script_path() -> Path:
    """Return the installed helper Python script path."""
    return helper_install_dir() / "macos_cdp_helper.py"


def helper_launcher_script_path() -> Path:
    """Return the installed helper launcher shell script path."""
    return helper_install_dir() / "run-helper.sh"


def helper_stdout_log_path() -> Path:
    return helper_install_dir() / "helper.stdout.log"


def helper_stderr_log_path() -> Path:
    return helper_install_dir() / "helper.stderr.log"


def expand_home(path_value: str) -> Path:
    """Expand a leading ~/ prefix without delegating to pathlib home expansion."""
    raw = str(path_value or "").strip()
    if raw.startswith("~/"):
        return Path.home() / raw[2:]
    return Path(raw)


def candidate_mac_chrome_paths() -> list[Path]:
    """Return likely Chrome-family app binaries on macOS."""
    home = Path.home()
    return [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        home / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome",
        home / "Applications" / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
        home / "Applications" / "Microsoft Edge.app" / "Contents" / "MacOS" / "Microsoft Edge",
    ]


def resolve_chrome_path(configured_path: str = "") -> Path:
    """Return a usable Chrome-family binary on macOS."""
    configured = str(configured_path or "").strip()
    if configured:
        path = Path(configured)
        if path.exists():
            return path
        raise FileNotFoundError(f"Configured Chrome executable does not exist: {path}")

    for candidate in candidate_mac_chrome_paths():
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "No Chrome/Chromium executable was found on this Mac. "
        "Install Chrome/Chromium or set WEB_CDP_CHROME_PATH."
    )


def parse_cdp_endpoint(endpoint_url: str) -> tuple[str, int]:
    """Extract host/port from a CDP endpoint URL."""
    parsed = parse.urlparse(endpoint_url)
    host = parsed.hostname or DEFAULT_HELPER_HOST
    port = int(parsed.port or 9222)
    return host, port


def cdp_json_version_url(endpoint_url: str) -> str:
    """Return the JSON version endpoint for a CDP base URL."""
    base = endpoint_url.rstrip("/")
    return f"{base}/json/version"


def local_cdp_endpoint(endpoint_url: str) -> str:
    """Return the host-local loopback endpoint for a requested CDP port."""
    _, port = parse_cdp_endpoint(endpoint_url)
    return f"http://{DEFAULT_HELPER_HOST}:{port}"


def request_helper_health(helper_url: str = DEFAULT_HELPER_URL, timeout_s: float = 1.0) -> bool:
    """Return True when the helper is accepting loopback HTTP requests."""
    try:
        with request.urlopen(f"{helper_url.rstrip('/')}/healthz", timeout=timeout_s) as response:
            return response.status == 200
    except Exception:
        return False


def request_helper_ensure(
    helper_url: str,
    *,
    endpoint_url: str,
    profile_dir: str,
    start_url: str = DEFAULT_START_URL,
    chrome_path: str = "",
    force_new_window: bool = False,
    timeout_s: float = 20.0,
) -> dict[str, str]:
    """Ask the helper to reuse or launch a host CDP browser."""
    payload = {
        "endpointUrl": endpoint_url,
        "profileDir": profile_dir,
        "startUrl": start_url,
        "chromePath": chrome_path,
        "forceNewWindow": bool(force_new_window),
    }
    req = request.Request(
        f"{helper_url.rstrip('/')}/v1/cdp/ensure",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_s) as response:
        data = json.loads(response.read().decode("utf-8") or "{}")
    return {
        "status": str(data.get("status") or "failed"),
        "detail": str(data.get("detail") or ""),
        "endpointUrl": str(data.get("endpointUrl") or endpoint_url),
    }


def is_cdp_endpoint_reachable(endpoint_url: str, timeout_s: float = 1.0) -> bool:
    """Return True when a CDP endpoint responds to /json/version."""
    try:
        with request.urlopen(cdp_json_version_url(endpoint_url), timeout=timeout_s) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_cdp_endpoint(endpoint_url: str, timeout_s: float = 15.0, interval_s: float = 0.5) -> bool:
    """Wait until a CDP endpoint becomes reachable."""
    deadline = time.time() + max(timeout_s, interval_s)
    while time.time() < deadline:
        if is_cdp_endpoint_reachable(endpoint_url, timeout_s=min(interval_s, 1.0)):
            return True
        time.sleep(interval_s)
    return False


def ensure_cdp_browser(
    *,
    endpoint_url: str,
    profile_dir: str,
    start_url: str = DEFAULT_START_URL,
    chrome_path: str = "",
    force_new_window: bool = False,
    timeout_s: float = 15.0,
) -> dict[str, str]:
    """Reuse an existing CDP endpoint or launch a new host Chrome window."""
    local_endpoint_url = local_cdp_endpoint(endpoint_url)

    if not force_new_window and is_cdp_endpoint_reachable(local_endpoint_url, timeout_s=1.0):
        return {
            "status": "reused",
            "detail": f"Reused existing CDP browser at {endpoint_url}.",
            "endpointUrl": endpoint_url,
        }

    try:
        chrome = resolve_chrome_path(chrome_path)
    except FileNotFoundError as exc:
        return {
            "status": "failed",
            "detail": str(exc),
            "endpointUrl": endpoint_url,
        }

    _, port = parse_cdp_endpoint(endpoint_url)
    profile_path = expand_home(profile_dir).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)

    command = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={DEFAULT_HELPER_HOST}",
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        start_url,
    ]

    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError:
        return {
            "status": "failed",
            "detail": f"Chrome executable was not found: {chrome}",
            "endpointUrl": endpoint_url,
        }
    except OSError as exc:
        return {
            "status": "failed",
            "detail": f"Failed to launch Chrome for CDP: {exc}",
            "endpointUrl": endpoint_url,
        }

    if wait_for_cdp_endpoint(local_endpoint_url, timeout_s=timeout_s):
        return {
            "status": "launched",
            "detail": (
                f"Chrome window opened with profile {profile_path}. "
                "If WhatsApp Web is not logged in yet, scan the QR code in that window and retry sync."
            ),
            "endpointUrl": endpoint_url,
        }

    return {
        "status": "failed",
        "detail": (
            f"Chrome was launched but CDP did not become reachable at {endpoint_url}. "
            "Chrome may still be starting, the profile may be locked, or another browser owns that profile."
        ),
        "endpointUrl": endpoint_url,
    }


class _HelperHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class _HelperRequestHandler(BaseHTTPRequestHandler):
    server_version = "NanobotMacCDPHelper/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"status": "failed", "detail": "Not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/cdp/ensure":
            self._send_json(404, {"status": "failed", "detail": "Not found"})
            return

        try:
            raw_length = self.headers.get("Content-Length", "0")
            length = int(raw_length)
            body = self.rfile.read(max(length, 0)).decode("utf-8") if length > 0 else "{}"
            payload = json.loads(body or "{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(400, {"status": "failed", "detail": "Invalid JSON payload"})
            return

        result = ensure_cdp_browser(
            endpoint_url=str(payload.get("endpointUrl") or f"http://{DEFAULT_HELPER_HOST}:9222"),
            profile_dir=str(payload.get("profileDir") or str(Path.home() / "whatsapp-web")),
            start_url=str(payload.get("startUrl") or DEFAULT_START_URL),
            chrome_path=str(payload.get("chromePath") or ""),
            force_new_window=bool(payload.get("forceNewWindow")),
        )
        self._send_json(200, result)


def serve(*, host: str = DEFAULT_HELPER_HOST, port: int = DEFAULT_HELPER_PORT) -> None:
    """Run the long-lived loopback helper HTTP server."""
    server = _HelperHTTPServer((host, port), _HelperRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _launcher_script_contents(python_candidates: list[str]) -> str:
    lines = [
        "#!/bin/sh",
        "set -eu",
        'export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"',
        f'HELPER_PY={shlex.quote(str(helper_python_script_path()))}',
    ]
    for candidate in python_candidates:
        lines.extend(
            [
                f'if [ -x {shlex.quote(candidate)} ]; then',
                f'  exec {shlex.quote(candidate)} "$HELPER_PY" serve --host {DEFAULT_HELPER_HOST} --port {DEFAULT_HELPER_PORT}',
                "fi",
            ]
        )
    lines.extend(
        [
            'echo "python3 not found for macOS CDP helper" >&2',
            "exit 127",
            "",
        ]
    )
    return "\n".join(lines)


def _launchctl_domain() -> str:
    return f"gui/{os.getuid()}"


def install_launchd_helper() -> dict[str, str]:
    """Install and start the per-user launchd helper on macOS."""
    if os.uname().sysname != "Darwin":
        raise RuntimeError("The macOS CDP helper can only be installed on macOS.")

    install_dir = helper_install_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    helper_launch_agent_path().parent.mkdir(parents=True, exist_ok=True)

    source_path = Path(__file__).resolve()
    helper_python_script_path().write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")

    python_candidates = []
    for candidate in [sys.executable, "/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]:
        if candidate and candidate not in python_candidates:
            python_candidates.append(candidate)

    launcher_script = helper_launcher_script_path()
    launcher_script.write_text(_launcher_script_contents(python_candidates), encoding="utf-8")
    launcher_script.chmod(0o755)

    plist_payload = {
        "Label": HELPER_LABEL,
        "ProgramArguments": [str(launcher_script)],
        "RunAtLoad": True,
        "KeepAlive": True,
        "WorkingDirectory": str(install_dir),
        "StandardOutPath": str(helper_stdout_log_path()),
        "StandardErrorPath": str(helper_stderr_log_path()),
    }
    with helper_launch_agent_path().open("wb") as handle:
        plistlib.dump(plist_payload, handle)

    domain = _launchctl_domain()
    subprocess.run(
        ["launchctl", "bootout", domain, str(helper_launch_agent_path())],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        ["launchctl", "bootstrap", domain, str(helper_launch_agent_path())],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"{domain}/{HELPER_LABEL}"],
        check=False,
        capture_output=True,
        text=True,
    )

    if not wait_for_helper(DEFAULT_HELPER_URL, timeout_s=5.0):
        raise RuntimeError(
            "Installed the macOS CDP helper, but it did not become healthy on "
            f"{DEFAULT_HELPER_URL}. Check the helper log files under {install_dir}."
        )

    return {
        "install_dir": str(install_dir),
        "helper_script": str(helper_python_script_path()),
        "launcher_script": str(launcher_script),
        "launch_agent": str(helper_launch_agent_path()),
        "helper_url": DEFAULT_HELPER_URL,
    }


def wait_for_helper(helper_url: str = DEFAULT_HELPER_URL, timeout_s: float = 5.0) -> bool:
    """Wait until the helper reports healthy."""
    deadline = time.time() + max(timeout_s, 0.2)
    while time.time() < deadline:
        if request_helper_health(helper_url, timeout_s=0.5):
            return True
        time.sleep(0.2)
    return False

def build_cli_parser() -> argparse.ArgumentParser:
    """Return the helper CLI parser."""
    parser = argparse.ArgumentParser(description="Nanobot macOS CDP helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the helper HTTP server")
    serve_parser.add_argument("--host", default=DEFAULT_HELPER_HOST)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_HELPER_PORT)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the helper CLI."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(host=args.host, port=args.port)
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
