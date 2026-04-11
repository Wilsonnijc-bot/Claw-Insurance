"""Configuration loading utilities.

Path confinement: config is loaded from the project-local ``config.json``
unless an explicit env var override (``NANOBOT_CONFIG_PATH`` or
``NANOBOT_APP_CONFIG_PATH``) is set.  The legacy ``~/.nanobot`` migration
has been removed.  If you still have state there from an older install, run
the standalone ``scripts/migrate_from_home.py`` script once.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from nanobot.config.schema import Config
from nanobot.config.supabase_loader import (
    has_external_supabase_config,
    load_supabase_config,
    save_supabase_config,
)
from nanobot.utils.paths import confine_path, project_root

logger = logging.getLogger(__name__)


def _project_config_path() -> Path:
    """Return the project-local config path."""
    return project_root() / "config.json"


def get_config_path() -> Path:
    """Get the preferred configuration file path.

    Lookup order:
    1. ``NANOBOT_CONFIG_PATH`` env var  (explicit override, may be external)
    2. ``NANOBOT_APP_CONFIG_PATH`` env var  (explicit override, may be external)
    3. Project-local ``config.json``

    Env var overrides are the *only* way to point config outside this repo.
    """
    explicit = os.environ.get("NANOBOT_CONFIG_PATH")
    if explicit:
        p = Path(explicit)
        # Log a warning when the explicit path is outside the project tree
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
    """Return candidate config paths in lookup order.

    Search is intentionally project-local unless an explicit env var or path is
    provided by the caller.
    """
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


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from file or create default.

    No legacy migration is performed.  Config is resolved strictly from
    the project-local ``config.json`` or an explicit env-var override.
    """
    for path in get_config_search_paths(config_path):
        try:
            data = _read_optional_json_object(path, label="config.json")
            split_catalog = load_supabase_config(path)
            if data is None and not split_catalog:
                continue
            data = _migrate_config(data or {})
            data = _merge_split_configs(data, catalog=split_catalog)
            return Config.model_validate(data)
        except ValueError as e:
            print(f"Warning: Failed to load config from {path}: {e}")
            print("Using default configuration.")
            break

    return Config()


def save_config(config: Config, config_path: Path | None = None) -> None:
    """
    Save configuration to file.

    Args:
        config: Configuration to save.
        config_path: Optional path to save to. Uses default if not provided.
    """
    path = get_config_search_paths(config_path)[0]
    path.parent.mkdir(parents=True, exist_ok=True)

    data = config.model_dump(by_alias=True)
    externalized_catalog = has_external_supabase_config(path)
    catalog_data = data.pop("catalog", None) if externalized_catalog else None

    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    if externalized_catalog:
        save_supabase_config(catalog_data or {}, path)


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


def _merge_split_configs(data: dict[str, Any], *, catalog: dict[str, Any]) -> dict[str, Any]:
    if not catalog:
        return data
    merged = dict(data)
    current_catalog = merged.get("catalog")
    base_catalog = dict(current_catalog) if isinstance(current_catalog, dict) else {}
    base_catalog.update(catalog)
    merged["catalog"] = base_catalog
    return merged


def _migrate_config(data: dict) -> dict:
    """Migrate old config formats to current."""
    # Move tools.exec.restrictToWorkspace → tools.restrictToWorkspace
    tools = data.get("tools", {})
    exec_cfg = tools.get("exec", {})
    if "restrictToWorkspace" in exec_cfg and "restrictToWorkspace" not in tools:
        tools["restrictToWorkspace"] = exec_cfg.pop("restrictToWorkspace")
    return data
