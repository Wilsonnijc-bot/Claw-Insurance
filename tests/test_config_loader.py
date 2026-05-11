from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.config.errors import ConfigLayoutError
from nanobot.config.loader import get_config_path, get_config_search_paths, load_config, save_config
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
    default_config.write_text(json.dumps({"gateway": {"port": 1111}}), encoding="utf-8")
    app_config.write_text(json.dumps({"gateway": {"port": 2222}}), encoding="utf-8")

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = load_config()

    assert config.gateway.port == 2222


def test_load_config_falls_back_to_default_when_app_config_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(tmp_path / "missing" / "nanobot.json"))

    search_paths = get_config_search_paths()
    home_nanobot = Path.home() / ".nanobot"
    for path in search_paths:
        assert not str(path).startswith(str(home_nanobot)), (
            f"Search path {path} points into legacy ~/.nanobot"
        )


def test_save_config_writes_non_default_catalog_only_to_canonical_supabase_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    canonical_supabase_config = app_dir / "supabase.json"
    app_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = Config.model_validate(
        {
            "gateway": {"port": 4567},
            "catalog": {
                "supabaseUrl": "https://split.supabase.co",
                "supabaseCatalogTables": ["insurance_products"],
            },
        }
    )
    save_config(config)

    saved_main = json.loads(app_config.read_text(encoding="utf-8"))
    saved_split = json.loads(canonical_supabase_config.read_text(encoding="utf-8"))

    assert saved_main["gateway"]["port"] == 4567
    assert "catalog" not in saved_main
    assert saved_split["supabaseUrl"] == "https://split.supabase.co"
    assert saved_split["supabaseCatalogTables"] == ["insurance_products"]


def test_save_config_removes_canonical_supabase_file_when_catalog_is_default(
    monkeypatch,
    tmp_path: Path,
) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    canonical_supabase_config = app_dir / "supabase.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    canonical_supabase_config.write_text(
        json.dumps({"supabaseUrl": "https://split.supabase.co"}),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    save_config(Config())

    assert app_config.exists()
    assert not canonical_supabase_config.exists()


def test_load_config_reads_canonical_supabase_file(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text(json.dumps({"gateway": {"port": 3456}}), encoding="utf-8")
    (app_dir / "supabase.json").write_text(
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

    assert config.gateway.port == 3456
    assert config.catalog.supabase_url == "https://split.supabase.co"
    assert config.catalog.supabase_catalog_tables == ["insurance_products", "dental_insurance"]
    assert config.catalog.cache_ttl_seconds == 120


def test_load_config_rejects_inline_catalog_in_main_config(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text(
        json.dumps({"catalog": {"supabaseUrl": "https://legacy.supabase.co"}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    with pytest.raises(ConfigLayoutError, match="unsupported 'catalog' settings"):
        load_config()


def test_load_config_rejects_legacy_supabaseconfig_file(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")
    (app_dir / "supabaseconfig.json").write_text(
        json.dumps({"supabaseUrl": "https://legacy.supabase.co"}),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    with pytest.raises(ConfigLayoutError, match="Unsupported legacy Supabase config file found"):
        load_config()


def test_load_config_rejects_legacy_googleconfig_file(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")
    (app_dir / "googleconfig.json").write_text(
        json.dumps({"projectId": "legacy-project"}),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    with pytest.raises(ConfigLayoutError, match="Unsupported legacy Google config file found"):
        load_config()


def test_load_config_rejects_nested_catalog_in_supabase_json(monkeypatch, tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")
    (app_dir / "supabase.json").write_text(
        json.dumps({"catalog": {"supabaseUrl": "https://split.supabase.co"}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    with pytest.raises(ConfigLayoutError, match="top-level Supabase catalog fields only"):
        load_config()


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


def test_pyproject_sdist_includes_canonical_example_files_only() -> None:
    content = Path("pyproject.toml").read_text(encoding="utf-8")

    assert '"google.example.json"' in content
    assert '"supabase.example.json"' in content
    assert '"googleconfig.example.json"' not in content
    assert '"supabaseconfig.example.json"' not in content
