"""Configuration loading utilities."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from nanobot.config.errors import ConfigLayoutError
from nanobot.config.schema import Config
from nanobot.config.supabase_loader import (
    get_supabase_config_path,
    load_supabase_config,
    save_supabase_config,
)
from nanobot.utils.paths import confine_path, project_root

logger = logging.getLogger(__name__)


def _project_config_path() -> Path:
    """Return the project-local config path."""
    return project_root() / "config.json"


def get_config_path() -> Path:
    """Get the preferred configuration file path."""
    explicit = os.environ.get("NANOBOT_CONFIG_PATH")
    if explicit:
        p = Path(explicit)
        try:
            confine_path(p)
        except ValueError:
            logger.warning(
                "NANOBOT_CONFIG_PATH points outside project root (explicit override): %s", p
            )
        return p.resolve()

    app_path = os.environ.get("NANOBOT_APP_CONFIG_PATH")
    if app_path:
        p = Path(app_path)
        try:
            confine_path(p)
        except ValueError:
            logger.warning(
                "NANOBOT_APP_CONFIG_PATH points outside project root (explicit override): %s", p
            )
        return p.resolve()

    return _project_config_path()


def get_config_search_paths(config_path: Path | None = None) -> list[Path]:
    """Return candidate config paths in lookup order."""
    if config_path is not None:
        return [config_path.resolve()]

    paths: list[Path] = []
    explicit = os.environ.get("NANOBOT_CONFIG_PATH")
    if explicit:
        paths.append(Path(explicit).resolve())
    else:
        app_path = os.environ.get("NANOBOT_APP_CONFIG_PATH")
        if app_path:
            paths.append(Path(app_path).resolve())
        paths.append(_project_config_path())

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def get_data_dir() -> Path:
    """Get the nanobot data directory."""
    from nanobot.utils.helpers import get_data_path

    return get_data_path()


def assert_canonical_split_config_layout(config_path: Path | None = None) -> None:
    """Reject legacy split config files and inline catalog config."""
    path = get_config_search_paths(config_path)[0]
    _assert_no_legacy_split_files(path)
    try:
        data = _read_optional_json_object(path, label="config.json")
    except ValueError:
        return
    _assert_no_inline_catalog_data(data, path)


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from canonical config files or create default."""
    for path in get_config_search_paths(config_path):
        try:
            data = _read_optional_json_object(path, label="config.json")
            _assert_no_legacy_split_files(path)
            _assert_no_inline_catalog_data(data, path)
            split_catalog = load_supabase_config(path)
            if data is None and not split_catalog:
                continue
            data = _migrate_config(data or {})
            if split_catalog:
                data = dict(data)
                data["catalog"] = dict(split_catalog)
            return Config.model_validate(data)
        except ConfigLayoutError:
            raise
        except ValueError as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")
            break

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """Save configuration to canonical config files."""
    path = get_config_search_paths(config_path)[0]
    assert_canonical_split_config_layout(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    catalog_payload = config.catalog.model_dump(
        by_alias=True,
        exclude_defaults=True,
        exclude_none=True,
    )
    data = config.model_dump(by_alias=True)
    data.pop("catalog", None)

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    supabase_path = get_supabase_config_path(path)
    if catalog_payload:
        save_supabase_config(catalog_payload, path)
    else:
        supabase_path.unlink(missing_ok=True)


def _read_optional_json_object(path: Path, *, label: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return payload


def _assert_no_legacy_split_files(config_path: Path) -> None:
    base_dir = config_path.resolve().parent
    legacy_google_path = base_dir / "googleconfig.json"
    legacy_supabase_path = base_dir / "supabaseconfig.json"
    google_path = base_dir / "google.json"
    supabase_path = base_dir / "supabase.json"

    if legacy_google_path.exists():
        raise ConfigLayoutError(
            "Unsupported legacy Google config file found: "
            f"{legacy_google_path}. Rename or move it to {google_path} and use google.example.json as the template."
        )
    if legacy_supabase_path.exists():
        raise ConfigLayoutError(
            "Unsupported legacy Supabase config file found: "
            f"{legacy_supabase_path}. Move these settings into {supabase_path} and use supabase.example.json as the template."
        )


def _assert_no_inline_catalog_data(data: dict[str, Any] | None, path: Path) -> None:
    if not data or "catalog" not in data:
        return
    catalog = data.get("catalog")
    if isinstance(catalog, dict) and not catalog:
        return
    raise ConfigLayoutError(
        "config.json contains unsupported 'catalog' settings: "
        f"{path}. Move them into {get_supabase_config_path(path)} and keep config.json for core app settings only."
    )


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
