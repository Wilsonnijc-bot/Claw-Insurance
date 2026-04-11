"""Isolated Google Speech-to-Text configuration loader."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.utils.paths import project_root

GOOGLE_CONFIG_FILENAME = "google.json"
LEGACY_GOOGLE_CONFIG_FILENAME = "googleconfig.json"


def _default_google_config_path(root: Path) -> Path:
    """Return the preferred Google config path, with legacy fallback."""
    preferred = root / GOOGLE_CONFIG_FILENAME
    legacy = root / LEGACY_GOOGLE_CONFIG_FILENAME
    if preferred.exists() or not legacy.exists():
        return preferred
    return legacy


class GoogleConfigError(RuntimeError):
    """Raised when the Google config file or its credential file is invalid."""


@dataclass(frozen=True)
class GoogleSpeechConfig:
    """Validated Google STT settings loaded from the project Google config."""

    project_id: str
    location: str
    language_code: str
    model: str
    credential_json_path: Path
    config_path: Path

    @property
    def recognizer(self) -> str:
        return (
            f"projects/{self.project_id}/locations/{self.location}/recognizers/_"
        )

    @property
    def api_endpoint(self) -> str:
        return f"{self.location}-speech.googleapis.com"


def get_google_config_path() -> Path:
    """Return the project-local Google STT config path."""
    return _default_google_config_path(project_root())


def _read_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GoogleConfigError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GoogleConfigError(f"{label} is not valid JSON: {path}") from exc

    if not isinstance(payload, dict):
        raise GoogleConfigError(f"{label} must contain a JSON object: {path}")
    return payload


def _require_text(payload: dict[str, Any], field: str, *, label: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise GoogleConfigError(f"{label} missing required field '{field}'")
    return value.strip()


def _resolve_credential_path(config_path: Path, raw_path: str) -> Path:
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = (config_path.parent / candidate).resolve()
    else:
        candidate = candidate.resolve()
    return candidate


def _validate_project_local_path(path: Path) -> None:
    root = project_root().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GoogleConfigError(
            "Google credential file must stay inside this project root: "
            f"{root}. Update {GOOGLE_CONFIG_FILENAME} credentialJsonPath."
        ) from exc


def _validate_credential_file(path: Path) -> None:
    payload = _read_json_file(path, label="Google credential file")
    required_keys = ("type", "client_email", "private_key", "token_uri")
    missing = [key for key in required_keys if not str(payload.get(key) or "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise GoogleConfigError(
            f"Google credential file missing required field(s): {joined}"
        )


def load_google_config(config_path: Path | None = None) -> GoogleSpeechConfig:
    """Load and validate the Google config plus its credential file path."""
    path = (config_path or get_google_config_path()).resolve()
    label = path.name
    payload = _read_json_file(path, label=label)

    project_id = _require_text(payload, "projectId", label=label)
    location = _require_text(payload, "location", label=label)
    language_code = _require_text(payload, "languageCode", label=label)
    model = _require_text(payload, "model", label=label)
    credential_json_path = _require_text(
        payload,
        "credentialJsonPath",
        label=label,
    )

    if model != "chirp_3":
        raise GoogleConfigError(
            f"{label} field 'model' must be exactly 'chirp_3'"
        )

    credential_path = _resolve_credential_path(path, credential_json_path)
    _validate_project_local_path(credential_path)
    if not credential_path.exists():
        raise GoogleConfigError(
            "Google credential file not found at "
            f"{credential_path}. Update {label} credentialJsonPath."
        )

    _validate_credential_file(credential_path)

    return GoogleSpeechConfig(
        project_id=project_id,
        location=location,
        language_code=language_code,
        model=model,
        credential_json_path=credential_path,
        config_path=path,
    )
