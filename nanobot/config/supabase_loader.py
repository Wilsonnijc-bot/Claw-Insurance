"""Canonical Supabase catalog configuration loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.config.errors import ConfigLayoutError
from nanobot.utils.paths import project_root

SUPABASE_CONFIG_FILENAME = "supabase.json"


def _config_base_dir(config_path: Path | None = None) -> Path:
    if config_path is None:
        return project_root().resolve()
    resolved = config_path.resolve()
    return resolved.parent


def _legacy_supabase_config_path(config_path: Path | None = None) -> Path:
    return _config_base_dir(config_path) / "supabaseconfig.json"


def get_supabase_config_path(config_path: Path | None = None) -> Path:
    """Return the canonical Supabase config path next to the main config file."""
    if config_path is None:
        return _config_base_dir() / SUPABASE_CONFIG_FILENAME
    resolved = config_path.resolve()
    return resolved if resolved.name == SUPABASE_CONFIG_FILENAME else resolved.parent / SUPABASE_CONFIG_FILENAME


def has_external_supabase_config(config_path: Path | None = None) -> bool:
    """Return True when the canonical split Supabase config file exists."""
    return get_supabase_config_path(config_path).exists()


def load_supabase_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load the canonical split Supabase catalog config."""
    path = get_supabase_config_path(config_path)
    legacy_path = _legacy_supabase_config_path(config_path)

    if legacy_path.exists():
        raise ConfigLayoutError(
            "Unsupported legacy Supabase config file found: "
            f"{legacy_path}. Move these settings into {path} and use supabase.example.json as the template."
        )
    if not path.exists():
        return {}

    payload = _read_json_object(path, label=path.name)
    if "catalog" in payload:
        raise ConfigLayoutError(
            f"{path.name} must contain top-level Supabase catalog fields only. "
            f"Move nested 'catalog' fields to the top level: {path}"
        )
    return payload


def save_supabase_config(payload: dict[str, Any], config_path: Path | None = None) -> None:
    """Write the canonical split Supabase catalog config."""
    path = get_supabase_config_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain a JSON object: {path}")
    return payload
