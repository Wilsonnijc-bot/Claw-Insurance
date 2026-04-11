from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import nanobot.macos_cdp_helper as helper


def test_ensure_cdp_browser_reuses_existing_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(helper, "is_cdp_endpoint_reachable", lambda endpoint_url, timeout_s=1.0: True)

    result = helper.ensure_cdp_browser(
        endpoint_url="http://127.0.0.1:9222",
        profile_dir="/tmp/wa-profile",
    )

    assert result == {
        "status": "reused",
        "detail": "Reused existing CDP browser at http://127.0.0.1:9222.",
        "endpointUrl": "http://127.0.0.1:9222",
    }


def test_ensure_cdp_browser_launches_chrome_with_requested_profile(monkeypatch, tmp_path: Path) -> None:
    launched: dict[str, object] = {}

    monkeypatch.setattr(helper, "is_cdp_endpoint_reachable", lambda endpoint_url, timeout_s=1.0: False)
    monkeypatch.setattr(helper, "wait_for_cdp_endpoint", lambda endpoint_url, timeout_s=15.0, interval_s=0.5: True)
    monkeypatch.setattr(
        helper,
        "resolve_chrome_path",
        lambda configured_path="": tmp_path / "Google Chrome",
    )

    def fake_popen(command, stdout=None, stderr=None, start_new_session=None):
        launched["command"] = command
        launched["stdout"] = stdout
        launched["stderr"] = stderr
        launched["start_new_session"] = start_new_session
        return SimpleNamespace()

    monkeypatch.setattr(helper.subprocess, "Popen", fake_popen)

    profile_dir = tmp_path / "whatsapp-web"
    result = helper.ensure_cdp_browser(
        endpoint_url="http://127.0.0.1:9222",
        profile_dir=str(profile_dir),
    )

    assert result["status"] == "launched"
    assert result["endpointUrl"] == "http://127.0.0.1:9222"
    command = launched["command"]
    assert str(tmp_path / "Google Chrome") == command[0]
    assert "--remote-debugging-port=9222" in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert f"--user-data-dir={profile_dir.resolve()}" in command
    assert command[-1] == helper.DEFAULT_START_URL
    assert profile_dir.exists()


def test_ensure_cdp_browser_checks_loopback_when_docker_requests_host_docker_internal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    checked: list[str] = []
    waited: list[str] = []

    monkeypatch.setattr(
        helper,
        "is_cdp_endpoint_reachable",
        lambda endpoint_url, timeout_s=1.0: checked.append(endpoint_url) or False,
    )
    monkeypatch.setattr(
        helper,
        "wait_for_cdp_endpoint",
        lambda endpoint_url, timeout_s=15.0, interval_s=0.5: waited.append(endpoint_url) or True,
    )
    monkeypatch.setattr(
        helper,
        "resolve_chrome_path",
        lambda configured_path="": tmp_path / "Google Chrome",
    )
    monkeypatch.setattr(
        helper.subprocess,
        "Popen",
        lambda command, stdout=None, stderr=None, start_new_session=None: SimpleNamespace(),
    )

    result = helper.ensure_cdp_browser(
        endpoint_url="http://host.docker.internal:9222",
        profile_dir=str(tmp_path / "whatsapp-web"),
    )

    assert result["status"] == "launched"
    assert checked == ["http://127.0.0.1:9222"]
    assert waited == ["http://127.0.0.1:9222"]
