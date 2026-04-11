import os
import signal
import inspect
from pathlib import Path

import pytest

import nanobot.cli.commands as commands
from nanobot.cli.commands import (
    _bridge_needs_refresh,
    _build_privacy_gateway_env,
    _build_whatsapp_bridge_env,
    _collect_nanobot_dev_runtime_pids,
    _stop_local_dev_runtime,
)
from nanobot.config.schema import Config


def test_build_whatsapp_bridge_env_uses_configured_values() -> None:
    config = Config.model_validate(
        {
            "channels": {
                "whatsapp": {
                    "enabled": True,
                    "bridgeUrl": "ws://127.0.0.1:3015",
                    "bridgeToken": "secret-token",
                    "webBrowserMode": "cdp",
                    "webCdpUrl": "http://127.0.0.1:9333",
                    "webCdpChromePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "webProfileDir": "~/wa-profile",
                    "allowFrom": ["*"],
                }
            }
        }
    )

    env = _build_whatsapp_bridge_env(config)

    assert env["BRIDGE_PORT"] == "3015"
    assert env["BRIDGE_TOKEN"] == "secret-token"
    assert env["WEB_BROWSER_MODE"] == "cdp"
    assert env["WEB_CDP_URL"] == "http://127.0.0.1:9333"
    assert env["WEB_CDP_CHROME_PATH"] == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert env["WEB_PROFILE_DIR"] == "~/wa-profile"
    assert env["PATH"] == os.environ["PATH"]


def test_build_whatsapp_bridge_env_prefers_runtime_env_in_source() -> None:
    source = inspect.getsource(commands._build_whatsapp_bridge_env)

    assert "runtime_value = os.environ.get(name)" in source
    assert '_set_runtime_value("WEB_BROWSER_MODE", wa.web_browser_mode)' in source
    assert '_set_runtime_value("WEB_CDP_URL", wa.web_cdp_url)' in source
    assert '_set_runtime_value("WEB_CDP_CHROME_PATH", wa.web_cdp_chrome_path)' in source
    assert '_set_runtime_value("WEB_PROFILE_DIR", wa.web_profile_dir)' in source


def test_whatsapp_web_gateway_entry_routes_to_gateway(monkeypatch) -> None:
    captured = {}

    def fake_app() -> None:
        captured["argv"] = list(commands.sys.argv)

    monkeypatch.setattr(commands, "app", fake_app)
    monkeypatch.setattr(commands.sys, "argv", ["whatsapp-web-nanobot-gateway", "--verbose"])

    commands.whatsapp_web_nanobot_gateway_entry()

    assert captured["argv"] == [
        "whatsapp-web-nanobot-gateway",
        "gateway",
        "--verbose",
    ]


def test_channels_whatsapp_web_reports_parse_managed_cdp(monkeypatch) -> None:
    printed: list[str] = []

    def fake_print(message: str, *args, **kwargs) -> None:
        printed.append(str(message))

    monkeypatch.setattr(commands.console, "print", fake_print)

    with pytest.raises(commands.typer.Exit) as exc:
        commands.channels_whatsapp_web()

    assert exc.value.exit_code == 1
    assert printed == [
        "[red]Standalone WhatsApp Web CDP launch is disabled. CDP is managed only during WhatsApp history parsing.[/red]"
    ]


def test_bridge_needs_refresh_when_source_is_newer(tmp_path: Path) -> None:
    source = tmp_path / "source"
    cached = tmp_path / "cached"
    (source / "src").mkdir(parents=True)
    (cached / "src").mkdir(parents=True)
    (cached / "dist").mkdir(parents=True)

    source_file = source / "src" / "whatsapp.ts"
    cached_file = cached / "src" / "whatsapp.ts"
    built_file = cached / "dist" / "index.js"

    source_file.write_text("new source\n", encoding="utf-8")
    cached_file.write_text("old cached source\n", encoding="utf-8")
    built_file.write_text("built bridge\n", encoding="utf-8")

    old_time = source_file.stat().st_mtime - 10
    os.utime(cached_file, (old_time, old_time))
    os.utime(built_file, (old_time, old_time))

    assert _bridge_needs_refresh(source, cached) is True


def test_build_privacy_gateway_env_uses_configured_values() -> None:
    config = Config.model_validate(
        {
            "agents": {"defaults": {"workspace": "~/privacy-workspace"}},
            "privacyGateway": {
                "enabled": True,
                "listenHost": "127.0.0.1",
                "listenPort": 8788,
                "failClosed": True,
                "saveRedactedDebug": False,
                "textOnlyScope": True,
                "enableNerAssist": True,
            },
        }
    )

    env = _build_privacy_gateway_env(config, "https://api.gptsapi.net/v1")

    assert env["NANOBOT_PRIVACY_UPSTREAM_BASE"] == "https://api.gptsapi.net/v1"
    assert env["NANOBOT_PRIVACY_WORKSPACE"] == str(config.workspace_path)
    assert env["NANOBOT_PRIVACY_LISTEN_HOST"] == "127.0.0.1"
    assert env["NANOBOT_PRIVACY_LISTEN_PORT"] == "8788"
    assert env["NANOBOT_PRIVACY_FAIL_CLOSED"] == "true"
    assert env["NANOBOT_PRIVACY_SAVE_REDACTED_DEBUG"] == "false"
    assert env["NANOBOT_PRIVACY_TEXT_ONLY_SCOPE"] == "true"
    assert env["NANOBOT_PRIVACY_ENABLE_NER_ASSIST"] == "true"


def test_collect_nanobot_dev_runtime_pids_captures_current_ui_flow(monkeypatch) -> None:
    process_table = {
        100: (50, "/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python -m nanobot ui"),
        101: (100, "npm run dev"),
        102: (101, "node /Users/nijiachen/Nanobot-Whatsapp/Insurance frontend/node_modules/.bin/vite"),
        103: (102, "/Users/nijiachen/Nanobot-Whatsapp/Insurance frontend/node_modules/@esbuild/darwin-arm64/bin/esbuild --service=0.21.5 --ping"),
        200: (60, "/Library/Frameworks/Python.framework/Versions/3.11/Resources/Python.app/Contents/MacOS/Python -m nanobot launcher --api-port 3456"),
        300: (70, "npm start"),
        301: (300, "node dist/index.js"),
        999: (1, "/bin/zsh -lc sleep 999"),
    }

    monkeypatch.setattr(commands, "_read_process_table", lambda: process_table)
    monkeypatch.setattr(commands, "_list_listening_pids", lambda ports: {102, 200, 301})

    targets = _collect_nanobot_dev_runtime_pids()

    assert targets == {
        100: process_table[100][1],
        101: process_table[101][1],
        102: process_table[102][1],
        103: process_table[103][1],
        200: process_table[200][1],
        300: process_table[300][1],
        301: process_table[301][1],
    }


def test_stop_local_dev_runtime_is_safe_when_nothing_matches(monkeypatch) -> None:
    monkeypatch.setattr(commands, "_collect_nanobot_dev_runtime_pids", lambda: {})

    matched, terminated, killed, remaining = _stop_local_dev_runtime(wait_seconds=0)

    assert matched == {}
    assert terminated == set()
    assert killed == set()
    assert remaining == set()


def test_stop_local_dev_runtime_escalates_to_sigkill_for_survivors(monkeypatch) -> None:
    monkeypatch.setattr(
        commands,
        "_collect_nanobot_dev_runtime_pids",
        lambda: {
            100: "Python -m nanobot ui",
            101: "npm run dev",
        },
    )

    calls: list[tuple[tuple[int, ...], int]] = []

    def fake_signal_pids(pids: set[int] | list[int], sig: int) -> set[int]:
        ordered = tuple(sorted(set(pids)))
        calls.append((ordered, sig))
        return set(ordered)

    live_checks = iter([{101}, set()])

    monkeypatch.setattr(commands, "_signal_pids", fake_signal_pids)
    monkeypatch.setattr(commands, "_live_pids", lambda pids: next(live_checks))
    monkeypatch.setattr(commands.time, "sleep", lambda _seconds: None)

    matched, terminated, killed, remaining = _stop_local_dev_runtime(wait_seconds=0)

    assert matched == {
        100: "Python -m nanobot ui",
        101: "npm run dev",
    }
    assert terminated == {100, 101}
    assert killed == {101}
    assert remaining == set()
    assert calls == [
        ((100, 101), signal.SIGTERM),
        ((101,), signal.SIGKILL),
    ]
