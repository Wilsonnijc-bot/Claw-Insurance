import os
from pathlib import Path

import nanobot.cli.commands as commands
from nanobot.cli.commands import (
    _bridge_needs_refresh,
    _build_whatsapp_cdp_launch_command,
    _build_privacy_gateway_env,
    _build_whatsapp_bridge_env,
    _cdp_probe_url,
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


def test_cdp_probe_url_normalizes_base_endpoint() -> None:
    assert _cdp_probe_url("http://127.0.0.1:9333") == "http://127.0.0.1:9333/json/version"


def test_build_whatsapp_cdp_launch_command_uses_configured_values() -> None:
    config = Config.model_validate(
        {
            "channels": {
                "whatsapp": {
                    "webBrowserMode": "cdp",
                    "webCdpUrl": "http://127.0.0.1:9333",
                    "webCdpChromePath": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "webProfileDir": "~/wa-profile",
                }
            }
        }
    )

    command = _build_whatsapp_cdp_launch_command(config)

    assert command[0] == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert "--remote-debugging-port=9333" in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--user-data-dir=~/wa-profile" in command
    assert command[-1] == "https://web.whatsapp.com/"


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
