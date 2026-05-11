"""Shared runtime utilities for Linux and Windows host CDP helpers."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
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

LINUX_HELPER_LABEL = "nanobot-host-cdp-helper"
WINDOWS_HELPER_LABEL = "Nanobot Host CDP Helper"


def helper_bind_host(platform_name: str) -> str:
    return "0.0.0.0"


def browser_bind_host(platform_name: str) -> str:
    return "0.0.0.0"


def helper_install_dir(platform_name: str) -> Path:
    home = Path.home()
    if platform_name == "windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "nanobot-host-cdp-helper"
        return home / "AppData" / "Local" / "nanobot-host-cdp-helper"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "nanobot-host-cdp-helper"
    return home / ".local" / "share" / "nanobot-host-cdp-helper"


def helper_python_script_path(platform_name: str) -> Path:
    return helper_install_dir(platform_name) / f"{platform_name}_cdp_helper.py"


def shared_runtime_script_path(platform_name: str) -> Path:
    return helper_install_dir(platform_name) / "_non_macos_cdp_helper.py"


def helper_launcher_script_path(platform_name: str) -> Path:
    suffix = ".cmd" if platform_name == "windows" else ".sh"
    return helper_install_dir(platform_name) / f"run-helper{suffix}"


def helper_stdout_log_path(platform_name: str) -> Path:
    return helper_install_dir(platform_name) / "helper.stdout.log"


def helper_stderr_log_path(platform_name: str) -> Path:
    return helper_install_dir(platform_name) / "helper.stderr.log"


def helper_token_path(platform_name: str) -> Path:
    return helper_install_dir(platform_name) / "helper.token"


def linux_systemd_service_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "systemd" / "user" / f"{LINUX_HELPER_LABEL}.service"


def linux_autostart_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / "autostart" / f"{LINUX_HELPER_LABEL}.desktop"


def windows_task_name() -> str:
    return WINDOWS_HELPER_LABEL


def expand_home(path_value: str) -> Path:
    raw = str(path_value or "").strip()
    if raw.startswith("~/"):
        return Path.home() / raw[2:]
    return Path(raw)


def _looks_like_path(value: str) -> bool:
    return "/" in value or "\\" in value


def _path_or_which(candidate: str | Path) -> Path | None:
    raw = str(candidate or "").strip()
    if not raw:
        return None
    if _looks_like_path(raw):
        path = Path(raw)
        return path if path.exists() else None
    resolved = shutil.which(raw)
    return Path(resolved) if resolved else None


def candidate_chrome_paths(platform_name: str) -> list[str]:
    home = Path.home()
    if platform_name == "windows":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_app_data = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        return [
            os.environ.get("CHROME_PATH", ""),
            str(Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            str(Path(program_files) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            str(Path(program_files_x86) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            "chrome.exe",
            "msedge.exe",
        ]

    return [
        os.environ.get("CHROME_PATH", ""),
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "msedge",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/usr/bin/microsoft-edge",
    ]


def resolve_chrome_path(configured_path: str = "", *, platform_name: str) -> Path:
    configured = str(configured_path or "").strip()
    if configured:
        resolved = _path_or_which(configured)
        if resolved is not None:
            return resolved
        raise FileNotFoundError(f"Configured Chrome executable does not exist: {configured}")

    for candidate in candidate_chrome_paths(platform_name):
        resolved = _path_or_which(candidate)
        if resolved is not None:
            return resolved

    raise FileNotFoundError(
        "No Chrome/Chromium executable was found on this host. "
        "Install Chrome/Chromium or set WEB_CDP_CHROME_PATH."
    )


def parse_cdp_endpoint(endpoint_url: str) -> tuple[str, int]:
    parsed = parse.urlparse(endpoint_url)
    host = parsed.hostname or DEFAULT_HELPER_HOST
    port = int(parsed.port or 9222)
    return host, port


def cdp_json_version_url(endpoint_url: str) -> str:
    return f"{endpoint_url.rstrip('/')}/json/version"


def local_cdp_endpoint(endpoint_url: str) -> str:
    _, port = parse_cdp_endpoint(endpoint_url)
    return f"http://{DEFAULT_HELPER_HOST}:{port}"


def load_helper_token(platform_name: str) -> str:
    token_file = helper_token_path(platform_name)
    if not token_file.exists():
        return ""
    return token_file.read_text(encoding="utf-8").strip()


def write_helper_token(token: str, *, platform_name: str) -> Path:
    token_file = helper_token_path(platform_name)
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(str(token or "").strip(), encoding="utf-8")
    if platform_name != "windows":
        token_file.chmod(0o600)
    return token_file


def load_or_create_helper_token(platform_name: str) -> str:
    token = load_helper_token(platform_name)
    if token:
        return token
    token = secrets.token_urlsafe(32)
    write_helper_token(token, platform_name=platform_name)
    return token


def request_helper_health(helper_url: str = DEFAULT_HELPER_URL, timeout_s: float = 1.0) -> bool:
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
    helper_token: str = "",
    timeout_s: float = 20.0,
) -> dict[str, str]:
    payload = {
        "endpointUrl": endpoint_url,
        "profileDir": profile_dir,
        "startUrl": start_url,
        "chromePath": chrome_path,
        "forceNewWindow": bool(force_new_window),
    }
    headers = {"Content-Type": "application/json"}
    if helper_token.strip():
        headers["Authorization"] = f"Bearer {helper_token.strip()}"
    req = request.Request(
        f"{helper_url.rstrip('/')}/v1/cdp/ensure",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
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
    try:
        with request.urlopen(cdp_json_version_url(endpoint_url), timeout=timeout_s) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_cdp_endpoint(endpoint_url: str, timeout_s: float = 15.0, interval_s: float = 0.5) -> bool:
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
    platform_name: str,
    start_url: str = DEFAULT_START_URL,
    chrome_path: str = "",
    force_new_window: bool = False,
    timeout_s: float = 15.0,
    bind_host: str = "",
) -> dict[str, str]:
    local_endpoint_url = local_cdp_endpoint(endpoint_url)
    if not force_new_window and is_cdp_endpoint_reachable(local_endpoint_url, timeout_s=1.0):
        return {
            "status": "reused",
            "detail": f"Reused existing CDP browser at {endpoint_url}.",
            "endpointUrl": endpoint_url,
        }

    try:
        chrome = resolve_chrome_path(chrome_path, platform_name=platform_name)
    except FileNotFoundError as exc:
        return {
            "status": "failed",
            "detail": str(exc),
            "endpointUrl": endpoint_url,
        }

    _, port = parse_cdp_endpoint(endpoint_url)
    profile_path = expand_home(profile_dir).resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    remote_host = bind_host.strip() or browser_bind_host(platform_name)

    command = [
        str(chrome),
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={remote_host}",
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


def _request_handler(platform_name: str):
    class _HelperRequestHandler(BaseHTTPRequestHandler):
        server_version = "NanobotHostCDPHelper/1.0"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _expected_token(self) -> str:
            return str(getattr(self.server, "helper_token", "") or "").strip()

        def _is_authorized(self) -> bool:
            expected = self._expected_token()
            if not expected:
                return True
            auth_header = str(self.headers.get("Authorization") or "").strip()
            provided = ""
            if auth_header.lower().startswith("bearer "):
                provided = auth_header[7:].strip()
            if not provided:
                provided = str(self.headers.get("X-Nanobot-Helper-Token") or "").strip()
            if provided == expected:
                return True
            self._send_json(401, {"status": "failed", "detail": "Unauthorized host CDP helper request."})
            return False

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send_json(200, {"status": "ok"})
                return
            self._send_json(404, {"status": "failed", "detail": "Not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/v1/cdp/ensure":
                self._send_json(404, {"status": "failed", "detail": "Not found"})
                return
            if not self._is_authorized():
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
                platform_name=platform_name,
            )
            self._send_json(200, result)

    return _HelperRequestHandler


def serve(
    *,
    platform_name: str,
    host: str | None = None,
    port: int = DEFAULT_HELPER_PORT,
    helper_token: str = "",
    token_file: str = "",
) -> None:
    resolved_token = str(helper_token or "").strip()
    if not resolved_token and str(token_file or "").strip():
        token_path = expand_home(str(token_file))
        if token_path.exists():
            resolved_token = token_path.read_text(encoding="utf-8").strip()
    server = _HelperHTTPServer((host or helper_bind_host(platform_name), port), _request_handler(platform_name))
    server.helper_token = resolved_token
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def wait_for_helper(helper_url: str = DEFAULT_HELPER_URL, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + max(timeout_s, 0.2)
    while time.time() < deadline:
        if request_helper_health(helper_url, timeout_s=0.5):
            return True
        time.sleep(0.2)
    return False


def _python_candidates(platform_name: str) -> list[str]:
    candidates: list[str] = []
    if sys.executable:
        candidates.append(sys.executable)
    exe = Path(sys.executable) if sys.executable else None
    if platform_name == "windows":
        if exe is not None:
            candidates.append(str(exe.with_name("pythonw.exe")))
            candidates.append(str(exe.with_name("python.exe")))
    else:
        candidates.extend(["/usr/local/bin/python3", "/usr/bin/python3", "python3"])
    deduped: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped


def _launcher_script_contents(
    python_candidates: list[str],
    *,
    helper_py: Path,
    host: str,
    port: int,
    token_file: Path | None,
) -> str:
    lines = [
        "#!/bin/sh",
        "set -eu",
        'export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"',
        f'HELPER_PY={shlex.quote(str(helper_py))}',
    ]
    if token_file is not None:
        lines.append(f'TOKEN_FILE={shlex.quote(str(token_file))}')
    for candidate in python_candidates:
        exec_line = (
            f'  exec {shlex.quote(candidate)} "$HELPER_PY" serve --host {shlex.quote(host)} '
            f'--port {port}'
        )
        if token_file is not None:
            exec_line += ' --token-file "$TOKEN_FILE"'
        lines.extend(
            [
                f'if [ -x {shlex.quote(candidate)} ] || command -v {shlex.quote(candidate)} >/dev/null 2>&1; then',
                exec_line,
                "fi",
            ]
        )
    lines.extend(
        [
            'echo "python runtime not found for Nanobot host CDP helper" >&2',
            "exit 127",
            "",
        ]
    )
    return "\n".join(lines)


def _windows_launcher_script_contents(
    python_candidates: list[str],
    *,
    helper_py: Path,
    host: str,
    port: int,
    token_file: Path | None,
) -> str:
    lines = [
        "@echo off",
        "setlocal",
        f'set "HELPER_PY={helper_py}"',
    ]
    if token_file is not None:
        lines.append(f'set "TOKEN_FILE={token_file}"')
    for candidate in python_candidates:
        candidate_path = candidate.replace("/", "\\")
        if not candidate_path or not (":" in candidate_path or "\\" in candidate_path):
            continue
        lines.append(f'if exist "{candidate_path}" (')
        launch = f'  "{candidate_path}" "%HELPER_PY%" serve --host {host} --port {port}'
        if token_file is not None:
            launch += ' --token-file "%TOKEN_FILE%"'
        lines.append(launch)
        lines.append("  exit /b %ERRORLEVEL%")
        lines.append(")")
    lines.extend(
        [
            'echo python runtime not found for Nanobot host CDP helper 1>&2',
            "exit /b 127",
            "",
        ]
    )
    return "\r\n".join(lines)


def _linux_systemd_unit_contents(launcher_script: Path, working_directory: Path) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Nanobot host CDP helper",
            "After=default.target graphical-session.target",
            "",
            "[Service]",
            f"WorkingDirectory={working_directory}",
            f"ExecStart=/bin/sh -lc {shlex.quote(str(launcher_script))}",
            "Restart=always",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _linux_autostart_contents(launcher_script: Path) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.0",
            "Name=Nanobot Host CDP Helper",
            f"Exec=/bin/sh -lc {shlex.quote(str(launcher_script))}",
            "X-GNOME-Autostart-enabled=true",
            "NoDisplay=true",
            "",
        ]
    )


def _write_helper_source_files(
    *,
    platform_name: str,
    helper_module_file: str,
    shared_module_file: str,
) -> tuple[Path, Path]:
    install_dir = helper_install_dir(platform_name)
    install_dir.mkdir(parents=True, exist_ok=True)
    helper_target = helper_python_script_path(platform_name)
    helper_target.write_text(Path(helper_module_file).read_text(encoding="utf-8"), encoding="utf-8")
    shared_target = shared_runtime_script_path(platform_name)
    shared_target.write_text(Path(shared_module_file).read_text(encoding="utf-8"), encoding="utf-8")
    return helper_target, shared_target


def _write_launcher_script(
    *,
    platform_name: str,
    helper_module_file: str,
    shared_module_file: str,
    host: str,
    helper_token: str = "",
) -> tuple[Path, Path | None]:
    helper_py, _shared_py = _write_helper_source_files(
        platform_name=platform_name,
        helper_module_file=helper_module_file,
        shared_module_file=shared_module_file,
    )
    install_dir = helper_install_dir(platform_name)
    install_dir.mkdir(parents=True, exist_ok=True)
    token_file: Path | None = None
    if helper_token:
        token_file = write_helper_token(helper_token, platform_name=platform_name)

    launcher_script = helper_launcher_script_path(platform_name)
    if platform_name == "windows":
        launcher_script.write_text(
            _windows_launcher_script_contents(
                _python_candidates(platform_name),
                helper_py=helper_py,
                host=host,
                port=DEFAULT_HELPER_PORT,
                token_file=token_file,
            ),
            encoding="utf-8",
        )
    else:
        launcher_script.write_text(
            _launcher_script_contents(
                _python_candidates(platform_name),
                helper_py=helper_py,
                host=host,
                port=DEFAULT_HELPER_PORT,
                token_file=token_file,
            ),
            encoding="utf-8",
        )
        launcher_script.chmod(0o755)
    return launcher_script, token_file


def _start_background_helper(launcher_script: Path, platform_name: str) -> None:
    if platform_name == "windows":
        creationflags = int(getattr(subprocess, "DETACHED_PROCESS", 0)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        subprocess.Popen(
            ["cmd.exe", "/c", str(launcher_script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        return

    subprocess.Popen(
        [str(launcher_script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def install_linux_helper(
    *,
    helper_module_file: str,
    shared_module_file: str,
    helper_token: str = "",
) -> dict[str, str]:
    install_dir = helper_install_dir("linux")
    install_dir.mkdir(parents=True, exist_ok=True)
    launcher_script, token_file = _write_launcher_script(
        platform_name="linux",
        helper_module_file=helper_module_file,
        shared_module_file=shared_module_file,
        host=helper_bind_host("linux"),
        helper_token=helper_token or load_or_create_helper_token("linux"),
    )

    service_path = linux_systemd_service_path()
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        _linux_systemd_unit_contents(launcher_script, install_dir),
        encoding="utf-8",
    )

    autostart_path = linux_autostart_path()
    autostart_path.parent.mkdir(parents=True, exist_ok=True)
    autostart_path.write_text(_linux_autostart_contents(launcher_script), encoding="utf-8")

    systemd_started = False
    if shutil.which("systemctl"):
        try:
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, text=True)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", service_path.name],
                check=True,
                capture_output=True,
                text=True,
            )
            systemd_started = True
        except subprocess.CalledProcessError:
            systemd_started = False

    if not systemd_started:
        _start_background_helper(launcher_script, "linux")

    if not wait_for_helper(DEFAULT_HELPER_URL, timeout_s=5.0):
        raise RuntimeError(
            "Installed the Linux host CDP helper, but it did not become healthy on "
            f"{DEFAULT_HELPER_URL}. Check the helper log files under {install_dir}."
        )

    return {
        "install_dir": str(install_dir),
        "helper_script": str(helper_python_script_path("linux")),
        "launcher_script": str(launcher_script),
        "service_file": str(service_path),
        "autostart_file": str(autostart_path),
        "helper_url": DEFAULT_HELPER_URL,
        "token_path": str(token_file) if token_file is not None else "",
    }


def _windows_task_command(launcher_script: Path) -> str:
    return subprocess.list2cmdline([str(launcher_script)])


def install_windows_helper(
    *,
    helper_module_file: str,
    shared_module_file: str,
    helper_token: str = "",
) -> dict[str, str]:
    install_dir = helper_install_dir("windows")
    install_dir.mkdir(parents=True, exist_ok=True)
    launcher_script, token_file = _write_launcher_script(
        platform_name="windows",
        helper_module_file=helper_module_file,
        shared_module_file=shared_module_file,
        host=helper_bind_host("windows"),
        helper_token=helper_token or load_or_create_helper_token("windows"),
    )

    task_name = windows_task_name()
    subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "schtasks",
            "/Create",
            "/SC",
            "ONLOGON",
            "/TN",
            task_name,
            "/TR",
            _windows_task_command(launcher_script),
            "/F",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["schtasks", "/Run", "/TN", task_name],
        check=False,
        capture_output=True,
        text=True,
    )

    if not wait_for_helper(DEFAULT_HELPER_URL, timeout_s=5.0):
        _start_background_helper(launcher_script, "windows")

    if not wait_for_helper(DEFAULT_HELPER_URL, timeout_s=5.0):
        raise RuntimeError(
            "Installed the Windows host CDP helper, but it did not become healthy on "
            f"{DEFAULT_HELPER_URL}. Check the helper log files under {install_dir}."
        )

    return {
        "install_dir": str(install_dir),
        "helper_script": str(helper_python_script_path("windows")),
        "launcher_script": str(launcher_script),
        "task_name": task_name,
        "helper_url": DEFAULT_HELPER_URL,
        "token_path": str(token_file) if token_file is not None else "",
    }


def build_cli_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install and start the host CDP helper")
    install_parser.add_argument("--json", action="store_true", help="Print the install result as JSON.")
    install_parser.add_argument(
        "--helper-token",
        default="",
        help="Optional helper token to enforce on POST requests.",
    )

    health_parser = subparsers.add_parser("health", help="Check whether the helper is healthy")
    health_parser.add_argument(
        "--helper-url",
        default=DEFAULT_HELPER_URL,
        help=f"Helper base URL to probe (default: {DEFAULT_HELPER_URL}).",
    )

    serve_parser = subparsers.add_parser("serve", help="Run the helper HTTP server")
    serve_parser.add_argument("--host", default=None)
    serve_parser.add_argument("--port", type=int, default=DEFAULT_HELPER_PORT)
    serve_parser.add_argument("--helper-token", default="")
    serve_parser.add_argument("--token-file", default="")

    return parser
