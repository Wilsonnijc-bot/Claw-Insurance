from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from nanobot.runtime import gateway_runtime


def _make_prebuilt_bridge(root: Path) -> Path:
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "index.js").write_text("console.log('ready')\n", encoding="utf-8")
    (root / "node_modules").mkdir()
    return root


def test_get_bridge_dir_prefers_image_prebuilt_bridge(monkeypatch, tmp_path: Path) -> None:
    bridge = _make_prebuilt_bridge(tmp_path / "bridge")
    monkeypatch.setenv("NANOBOT_PREBUILT_BRIDGE_DIR", str(bridge))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("runtime npm installation must not run for a prebuilt image")

    monkeypatch.setattr(gateway_runtime.subprocess, "run", fail_if_called)

    assert gateway_runtime.get_bridge_dir() == bridge.resolve()


def test_prebuilt_bridge_requires_built_output(monkeypatch, tmp_path: Path) -> None:
    bridge = tmp_path / "bridge"
    bridge.mkdir()
    monkeypatch.setenv("NANOBOT_PREBUILT_BRIDGE_DIR", str(bridge))

    with pytest.raises(RuntimeError, match="prebuilt WhatsApp bridge is incomplete"):
        gateway_runtime.get_bridge_dir()


def test_bridge_start_command_runs_compiled_entrypoint(monkeypatch, tmp_path: Path) -> None:
    bridge = _make_prebuilt_bridge(tmp_path / "bridge")
    monkeypatch.setattr(gateway_runtime.shutil, "which", lambda name: "/usr/bin/node")

    assert gateway_runtime.bridge_start_command(bridge) == [
        "/usr/bin/node",
        str(bridge / "dist" / "index.js"),
    ]


def _config(*, browser_mode: str) -> SimpleNamespace:
    whatsapp = SimpleNamespace(delivery_mode="draft", web_browser_mode=browser_mode)
    return SimpleNamespace(channels=SimpleNamespace(whatsapp=whatsapp))


def test_release_bridge_cdp_mode_never_installs_playwright(monkeypatch, tmp_path: Path) -> None:
    bridge = _make_prebuilt_bridge(tmp_path / "bridge")
    monkeypatch.setenv("NANOBOT_PREBUILT_BRIDGE_DIR", str(bridge))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("release startup must not download browser dependencies")

    monkeypatch.setattr(gateway_runtime.subprocess, "run", fail_if_called)
    gateway_runtime.ensure_whatsapp_bridge_browser(bridge, _config(browser_mode="cdp"), {})


def test_release_bridge_launch_mode_requires_custom_image(monkeypatch, tmp_path: Path) -> None:
    bridge = _make_prebuilt_bridge(tmp_path / "bridge")
    monkeypatch.setenv("NANOBOT_PREBUILT_BRIDGE_DIR", str(bridge))

    with pytest.raises(RuntimeError, match="cannot download Playwright Chromium"):
        gateway_runtime.ensure_whatsapp_bridge_browser(bridge, _config(browser_mode="launch"), {})
