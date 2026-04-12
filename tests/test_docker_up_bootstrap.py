from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import nanobot.docker_up_bootstrap as bootstrap


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


def test_run_docker_up_macos_installs_helper_and_launches_compose(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    installed: list[bool] = []

    monkeypatch.setattr(bootstrap, "docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap.sys, "platform", "darwin")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_docker_subprocess(captured))

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

    result = bootstrap.run_docker_up([])

    assert result == 0
    assert installed == [True]
    env = captured["env"]
    assert env["WEB_CDP_HELPER_PLATFORM"] == "macos"
    assert "WEB_CDP_HELPER_TOKEN" not in env
    assert env["WEB_HOST_PROFILE_DIR"] == str((tmp_path / "whatsapp-web").resolve())


def test_run_docker_up_linux_exports_helper_token(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(bootstrap, "docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_docker_subprocess(captured))
    monkeypatch.setenv("DISPLAY", ":0")

    import nanobot.linux_cdp_helper as helper

    monkeypatch.setattr(helper, "resolve_chrome_path", lambda configured_path="": tmp_path / "google-chrome")
    monkeypatch.setattr(helper, "load_or_create_helper_token", lambda: "linux-secret")
    monkeypatch.setattr(helper, "request_helper_health", lambda helper_url, timeout_s=0.5: True)
    monkeypatch.setattr(
        helper,
        "install_linux_helper",
        lambda helper_token="": (_ for _ in ()).throw(AssertionError("install should not run")),
    )

    result = bootstrap.run_docker_up(["nanobot-gateway"])

    assert result == 0
    env = captured["env"]
    assert env["WEB_CDP_HELPER_PLATFORM"] == "linux"
    assert env["WEB_CDP_HELPER_TOKEN"] == "linux-secret"
    assert captured["args"][-1] == "nanobot-gateway"


def test_run_docker_up_windows_installs_helper_and_launches_compose(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    installed: list[str] = []

    monkeypatch.setattr(bootstrap, "docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "C:/Docker/docker.exe" if name == "docker" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_docker_subprocess(captured))

    import nanobot.windows_cdp_helper as helper

    monkeypatch.setattr(helper, "resolve_chrome_path", lambda configured_path="": tmp_path / "chrome.exe")
    monkeypatch.setattr(helper, "load_or_create_helper_token", lambda: "windows-secret")
    monkeypatch.setattr(helper, "request_helper_health", lambda helper_url, timeout_s=0.5: False)
    monkeypatch.setattr(
        helper,
        "install_windows_helper",
        lambda helper_token="": installed.append(helper_token) or {
            "helper_url": helper.DEFAULT_HELPER_URL,
            "task_name": "Nanobot Host CDP Helper",
            "helper_script": "C:/helper/windows_cdp_helper.py",
        },
    )

    result = bootstrap.run_docker_up([])

    assert result == 0
    assert installed == ["windows-secret"]
    env = captured["env"]
    assert env["WEB_CDP_HELPER_PLATFORM"] == "windows"
    assert env["WEB_CDP_HELPER_TOKEN"] == "windows-secret"


def test_run_docker_up_rejects_headless_linux(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(bootstrap, "docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_docker_subprocess({}))
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)

    result = bootstrap.run_docker_up([])

    assert result == 1
    assert "desktop session" in capsys.readouterr().err


def test_run_docker_up_disables_sync_on_unsupported_platform(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(bootstrap, "docker_up_root_dir", lambda: tmp_path)
    monkeypatch.setattr(bootstrap.sys, "platform", "freebsd14")
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(bootstrap.subprocess, "run", _fake_docker_subprocess(captured))

    result = bootstrap.run_docker_up([])

    assert result == 0
    env = captured["env"]
    assert env["WEB_HISTORY_SYNC_ENABLED"] == "false"
    assert env["WEB_CDP_HELPER_URL"] == ""
    assert env["WEB_CDP_HELPER_PLATFORM"] == "freebsd14"


def test_bootstrap_module_parses_with_python310_grammar() -> None:
    source = Path(bootstrap.__file__).read_text(encoding="utf-8")
    ast.parse(source, filename=str(bootstrap.__file__), feature_version=(3, 10))
