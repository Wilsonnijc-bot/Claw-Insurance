import os
from pathlib import Path

from nanobot.cli.commands import (
    _bridge_needs_refresh,
    _build_privacy_gateway_env,
    _build_whatsapp_bridge_env,
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
                    "webProfileDir": "~/wa-profile",
                    "allowFrom": ["*"],
                }
            }
        }
    )

    env = _build_whatsapp_bridge_env(config)

    assert env["BRIDGE_PORT"] == "3015"
    assert env["BRIDGE_TOKEN"] == "secret-token"
    assert env["WEB_PROFILE_DIR"] == "~/wa-profile"
    assert env["PATH"] == os.environ["PATH"]


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
