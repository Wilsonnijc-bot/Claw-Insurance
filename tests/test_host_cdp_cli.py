from __future__ import annotations

from typer.testing import CliRunner

import nanobot.cli.commands as commands
import nanobot.docker_up_bootstrap as docker_up_bootstrap

runner = CliRunner()


def test_typer_docker_up_delegates_to_bootstrap(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run_docker_up(services):
        captured["services"] = list(services)
        return 0

    monkeypatch.setattr(docker_up_bootstrap, "run_docker_up", fake_run_docker_up)

    result = runner.invoke(commands.app, ["docker-up", "nanobot-gateway"])

    assert result.exit_code == 0
    assert captured["services"] == ["nanobot-gateway"]


def test_install_host_cdp_helper_dispatches_to_windows(monkeypatch) -> None:
    called: list[str] = []

    monkeypatch.setattr(commands.sys, "platform", "win32")
    monkeypatch.setattr(commands, "install_windows_cdp_helper", lambda: called.append("windows"))

    result = runner.invoke(commands.app, ["install-host-cdp-helper"])

    assert result.exit_code == 0
    assert called == ["windows"]
