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


def test_load_config_merges_split_supabase_file_over_legacy_catalog(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text(
        json.dumps(
            {
                "catalog": {
                    "supabaseUrl": "https://legacy.supabase.co",
                    "supabaseCatalogTable": "legacy_table",
                    "cacheTtlSeconds": 30,
                }
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "supabaseconfig.json").write_text(
        json.dumps(
            {
                "supabaseUrl": "https://split.supabase.co",
                "supabaseCatalogTables": ["insurance_products", "dental_insurance"],
                "cacheTtlSeconds": 120,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = load_config()

    assert config.catalog.supabase_url == "https://split.supabase.co"
    assert config.catalog.supabase_catalog_table == "legacy_table"
    assert config.catalog.supabase_catalog_tables == ["insurance_products", "dental_insurance"]
    assert config.catalog.cache_ttl_seconds == 120


def test_save_config_writes_split_supabase_file_when_externalized(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    supabase_config = app_dir / "supabaseconfig.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    supabase_config.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = Config.model_validate(
        {
            "catalog": {
                "supabaseUrl": "https://split.supabase.co",
                "supabaseCatalogTables": ["insurance_products"],
            }
        }
    )
    save_config(config)

    saved_main = json.loads(app_config.read_text(encoding="utf-8"))
    saved_split = json.loads(supabase_config.read_text(encoding="utf-8"))

    assert "catalog" not in saved_main
    assert saved_split["supabaseUrl"] == "https://split.supabase.co"
    assert saved_split["supabaseCatalogTables"] == ["insurance_products"]


def test_load_config_reads_legacy_supabaseconfig_fallback(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")
    (app_dir / "supabaseconfig.json").write_text(
        json.dumps(
            {
                "supabaseUrl": "https://legacy.supabase.co",
                "supabaseCatalogTables": ["legacy_table"],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = load_config()

    assert config.catalog.supabase_url == "https://legacy.supabase.co"
    assert config.catalog.supabase_catalog_tables == ["legacy_table"]


def test_config_example_uses_canonical_litellm_shape() -> None:
    payload = json.loads(Path("config.example.json").read_text(encoding="utf-8"))

    config = Config.model_validate(payload)

    assert config.agents.defaults.provider == "litellm"
    assert config.agents.defaults.model == "moonshot-v1-8k"
    assert config.gateway.port == 3456
    assert config.providers.litellm.base_url == "https://api.moonshot.cn/v1"
    assert config.channels.whatsapp.delivery_mode == "send"


def test_google_and_supabase_example_files_are_valid_json_objects() -> None:
    google_payload = json.loads(Path("google.example.json").read_text(encoding="utf-8"))
    supabase_payload = json.loads(Path("supabase.example.json").read_text(encoding="utf-8"))

    assert isinstance(google_payload, dict)
    assert google_payload["model"] == "chirp_3"
    assert isinstance(supabase_payload, dict)
    assert isinstance(supabase_payload["supabaseCatalogTables"], list)
