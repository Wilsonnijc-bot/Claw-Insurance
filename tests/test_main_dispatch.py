from __future__ import annotations

import ast
import builtins
import sys
import types
from pathlib import Path

import nanobot.__main__ as nanobot_main


def test_main_dispatches_docker_up_without_importing_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}
    bootstrap_module = types.ModuleType("nanobot.docker_up_bootstrap")

    def fake_bootstrap_main(args=None):
        captured["args"] = list(args or [])
        return 7

    bootstrap_module.main = fake_bootstrap_main
    monkeypatch.setitem(sys.modules, "nanobot.docker_up_bootstrap", bootstrap_module)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "nanobot.cli.commands":
            raise AssertionError("nanobot.cli.commands should not be imported for docker-up")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = nanobot_main.main(["docker-up", "nanobot-gateway"])

    assert result == 7
    assert captured["args"] == ["nanobot-gateway"]


def test_main_dispatches_other_commands_to_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}
    cli_module = types.ModuleType("nanobot.cli.commands")

    def fake_app(*, args=None, prog_name=None):
        captured["args"] = list(args or [])
        captured["prog_name"] = prog_name

    cli_module.app = fake_app
    monkeypatch.setitem(sys.modules, "nanobot.cli.commands", cli_module)

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "nanobot.docker_up_bootstrap":
            raise AssertionError("docker_up_bootstrap should not be imported for non-docker commands")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    result = nanobot_main.main(["status"])

    assert result == 0
    assert captured["args"] == ["status"]
    assert captured["prog_name"] == "nanobot"


def test_main_module_parses_with_python310_grammar() -> None:
    source = Path(nanobot_main.__file__).read_text(encoding="utf-8")
    ast.parse(source, filename=str(nanobot_main.__file__), feature_version=(3, 10))
