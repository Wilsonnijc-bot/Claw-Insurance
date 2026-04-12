from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import nanobot.cli.commands as commands

runner = CliRunner()


def _fake_docker_subprocess(captured: dict[str, object]):
    def fake_run(args, cwd=None, env=None, check=False, capture_output=False, text=False):
        argv = list(args)
        if argv[:3] == ["docker", "compose", "version"]:
            return SimpleNamespace(returncode=0, stdout="Docker Compose version v2", stderr="")
        if argv[:5] == ["docker", "compose", "up", "-d", "--build"]:
            captured["args"] = argv
            captured["cwd"] = cwd
            captured["env"] = env
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected subprocess.run call: {argv}")

    return fake_run


def test_docker_up_macos_keeps_existing_helper_flow(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    installed: list[bool] = []

    monkeypatch.setattr(commands, "_docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(commands.sys, "platform", "darwin")
    monkeypatch.setattr(commands.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(commands.subprocess, "run", _fake_docker_subprocess(captured))

    import nanobot.macos_cdp_helper as helper

    monkeypatch.setattr(helper, "resolve_chrome_path", lambda configured_path="": tmp_path / "Google Chrome")
    monkeypatch.setattr(helper, "request_helper_health", lambda helper_url, timeout_s=0.5: False)
    monkeypatch.setattr(
        helper,
        "install_launchd_helper",
        lambda: installed.append(True) or {
            "helper_url": helper.DEFAULT_HELPER_URL,
            "launch_agent": "/tmp/com.nanobot.macos-cdp-helper.plist",
            "helper_script": "/tmp/macos_cdp_helper.py",
            "launcher_script": "/tmp/run-helper.sh",
        },
    )

    result = runner.invoke(commands.app, ["docker-up"])

    assert result.exit_code == 0
    assert installed == [True]
    env = captured["env"]
    assert env["WEB_CDP_HELPER_PLATFORM"] == "macos"
    assert "WEB_CDP_HELPER_TOKEN" not in env
    assert env["WEB_HOST_PROFILE_DIR"] == str((tmp_path / "whatsapp-web").resolve())


def test_docker_up_linux_exports_helper_token(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(commands, "_docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(commands.sys, "platform", "linux")
    monkeypatch.setattr(commands.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(commands.subprocess, "run", _fake_docker_subprocess(captured))
    monkeypatch.setenv("DISPLAY", ":0")

    import nanobot.linux_cdp_helper as helper

    monkeypatch.setattr(helper, "resolve_chrome_path", lambda configured_path="": tmp_path / "google-chrome")
    monkeypatch.setattr(helper, "load_or_create_helper_token", lambda: "linux-secret")
    monkeypatch.setattr(helper, "request_helper_health", lambda helper_url, timeout_s=0.5: True)
    monkeypatch.setattr(helper, "install_linux_helper", lambda helper_token="": (_ for _ in ()).throw(AssertionError("install should not run")))

    result = runner.invoke(commands.app, ["docker-up", "nanobot-gateway"])

    assert result.exit_code == 0
    env = captured["env"]
    assert env["WEB_CDP_HELPER_PLATFORM"] == "linux"
    assert env["WEB_CDP_HELPER_TOKEN"] == "linux-secret"
    assert captured["args"][-1] == "nanobot-gateway"


def test_install_host_cdp_helper_dispatches_to_windows(monkeypatch) -> None:
    called: list[str] = []

    monkeypatch.setattr(commands.sys, "platform", "win32")
    monkeypatch.setattr(commands, "install_windows_cdp_helper", lambda: called.append("windows"))

    result = runner.invoke(commands.app, ["install-host-cdp-helper"])

    assert result.exit_code == 0
    assert called == ["windows"]
