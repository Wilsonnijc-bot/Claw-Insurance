"""Project-local Supabase catalog configuration loader."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.utils.paths import project_root


def get_supabase_config_path(config_path: Path | None = None) -> Path:
    """Return the Supabase catalog config path next to the main config file."""
    if config_path is None:
        return project_root() / "supabaseconfig.json"
    return config_path.resolve().parent / "supabaseconfig.json"


def has_external_supabase_config(config_path: Path | None = None) -> bool:
    """Return True when a split Supabase config file exists."""
    return get_supabase_config_path(config_path).exists()


def load_supabase_config(config_path: Path | None = None) -> dict[str, Any]:
    """Load the optional split Supabase catalog config."""
    path = get_supabase_config_path(config_path)
    if not path.exists():
        return {}

    payload = _read_json_object(path, label="supabaseconfig.json")
    if "catalog" in payload:
        catalog = payload.get("catalog")
        if not isinstance(catalog, dict):
            raise ValueError(f"supabaseconfig.json field 'catalog' must be a JSON object: {path}")
        payload = catalog
    return payload


def save_supabase_config(payload: dict[str, Any], config_path: Path | None = None) -> None:
    """Write the split Supabase catalog config."""
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
