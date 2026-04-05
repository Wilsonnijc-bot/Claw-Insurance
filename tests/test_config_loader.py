from __future__ import annotations

import json
from pathlib import Path

from nanobot.config.loader import get_config_path, load_config, save_config
from nanobot.config.schema import Config


def test_get_config_path_prefers_app_config_env(monkeypatch, tmp_path: Path) -> None:
    app_config = tmp_path / "app" / "nanobot.json"
    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    assert get_config_path() == app_config


def test_load_config_prefers_existing_app_config_path(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    default_config = home / ".nanobot" / "config.json"
    app_config = tmp_path / "app" / "nanobot.json"
    default_config.parent.mkdir(parents=True, exist_ok=True)
    app_config.parent.mkdir(parents=True, exist_ok=True)
    default_config.write_text(json.dumps({"catalog": {"supabaseCatalogTable": "default_table"}}), encoding="utf-8")
    app_config.write_text(json.dumps({"catalog": {"supabaseCatalogTable": "app_table"}}), encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = load_config()

    assert config.catalog.supabase_catalog_table == "app_table"


def test_load_config_falls_back_to_default_when_app_config_missing(monkeypatch, tmp_path: Path) -> None:
    """When NANOBOT_APP_CONFIG_PATH points to a non-existent file,
    the loader falls back to the project-local config.json (not ~/.nanobot).

    We verify the fallback chain does not contain any home-directory path.
    """
    from nanobot.config.loader import get_config_search_paths

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(tmp_path / "missing" / "nanobot.json"))

    search_paths = get_config_search_paths()
    home_nanobot = Path.home() / ".nanobot"
    for p in search_paths:
        assert not str(p).startswith(str(home_nanobot)), (
            f"Search path {p} points into legacy ~/.nanobot"
        )


def test_save_config_uses_app_config_path(monkeypatch, tmp_path: Path) -> None:
    app_config = tmp_path / "app" / "nanobot.json"
    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = Config.model_validate({"catalog": {"supabaseCatalogTable": "app_table"}})
    save_config(config)

    saved = json.loads(app_config.read_text(encoding="utf-8"))
    assert saved["catalog"]["supabaseCatalogTable"] == "app_table"
