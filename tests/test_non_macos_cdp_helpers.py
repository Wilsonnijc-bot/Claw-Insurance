from __future__ import annotations

import threading
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

import nanobot.linux_cdp_helper as linux_helper
import nanobot.windows_cdp_helper as windows_helper


def test_linux_helper_launches_chrome_with_docker_reachable_bind(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}

    monkeypatch.setattr(linux_helper._common, "is_cdp_endpoint_reachable", lambda endpoint_url, timeout_s=1.0: False)
    monkeypatch.setattr(linux_helper._common, "wait_for_cdp_endpoint", lambda endpoint_url, timeout_s=15.0, interval_s=0.5: True)
    monkeypatch.setattr(
        linux_helper._common,
        "resolve_chrome_path",
        lambda configured_path="", platform_name="linux": tmp_path / "google-chrome",
    )

    def fake_popen(command, stdout=None, stderr=None, start_new_session=None):
        launched["command"] = command
        launched["stdout"] = stdout
        launched["stderr"] = stderr
        launched["start_new_session"] = start_new_session
        return SimpleNamespace()

    monkeypatch.setattr(linux_helper._common.subprocess, "Popen", fake_popen)

    profile_dir = tmp_path / "whatsapp-web"
    result = linux_helper.ensure_cdp_browser(
        endpoint_url="http://host.docker.internal:9222",
        profile_dir=str(profile_dir),
    )

    assert result["status"] == "launched"
    command = launched["command"]
    assert str(tmp_path / "google-chrome") == command[0]
    assert "--remote-debugging-port=9222" in command
    assert "--remote-debugging-address=0.0.0.0" in command
    assert f"--user-data-dir={profile_dir.resolve()}" in command


def test_linux_helper_server_requires_token_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        linux_helper._common,
        "ensure_cdp_browser",
        lambda **kwargs: {
            "status": "launched",
            "detail": "Chrome window opened.",
            "endpointUrl": str(kwargs["endpoint_url"]),
        },
    )
    server = linux_helper._common._HelperHTTPServer(
        ("127.0.0.1", 0),
        linux_helper._common._request_handler("linux"),
    )
    server.helper_token = "secret-token"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    helper_url = f"http://127.0.0.1:{server.server_address[1]}"

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        linux_helper.request_helper_ensure(
            helper_url,
            endpoint_url="http://127.0.0.1:9222",
            profile_dir="/tmp/wa-profile",
        )
    assert exc_info.value.code == 401

    result = linux_helper.request_helper_ensure(
        helper_url,
        endpoint_url="http://127.0.0.1:9222",
        profile_dir="/tmp/wa-profile",
        helper_token="secret-token",
    )
    assert result["status"] == "launched"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def test_install_linux_helper_writes_service_and_autostart(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(linux_helper._common, "wait_for_helper", lambda helper_url=linux_helper.DEFAULT_HELPER_URL, timeout_s=5.0: True)
    monkeypatch.setattr(linux_helper._common.shutil, "which", lambda name: None)
    started: list[Path] = []
    monkeypatch.setattr(linux_helper._common, "_start_background_helper", lambda launcher_script, platform_name: started.append(launcher_script))

    result = linux_helper.install_linux_helper(helper_token="linux-secret")

    assert Path(result["helper_script"]).exists()
    assert Path(result["launcher_script"]).exists()
    assert Path(result["service_file"]).exists()
    assert Path(result["autostart_file"]).exists()
    assert Path(result["token_path"]).read_text(encoding="utf-8") == "linux-secret"
    assert started == [Path(result["launcher_script"])]
    assert "run-helper.sh" in Path(result["service_file"]).read_text(encoding="utf-8")
    assert "run-helper.sh" in Path(result["autostart_file"]).read_text(encoding="utf-8")


def test_install_windows_helper_creates_scheduled_task(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(
        windows_helper._common,
        "wait_for_helper",
        lambda helper_url=windows_helper.DEFAULT_HELPER_URL, timeout_s=5.0: True,
    )
    calls: list[list[str]] = []

    def fake_run(args, check=False, capture_output=False, text=False):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(windows_helper._common.subprocess, "run", fake_run)

    result = windows_helper.install_windows_helper(helper_token="windows-secret")

    assert Path(result["helper_script"]).exists()
    assert Path(result["launcher_script"]).exists()
    assert Path(result["token_path"]).read_text(encoding="utf-8") == "windows-secret"
    assert any(command[:2] == ["schtasks", "/Create"] for command in calls)
    assert any(command[:2] == ["schtasks", "/Run"] for command in calls)
