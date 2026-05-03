"""Canonical Google Speech-to-Text configuration loader."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nanobot.config.loader import get_config_path

GOOGLE_CONFIG_FILENAME = "google.json"


def _config_base_dir(config_path: Path | None = None) -> Path:
    anchor = config_path or get_config_path()
    return anchor.resolve().parent


def _legacy_google_config_path(config_path: Path | None = None) -> Path:
    return _config_base_dir(config_path) / "googleconfig.json"


def _resolve_google_config_path(config_path: Path | None = None) -> Path:
    return _config_base_dir(config_path) / GOOGLE_CONFIG_FILENAME


class GoogleConfigError(RuntimeError):
    """Raised when the Google config file or its credential file is invalid."""


@dataclass(frozen=True)
class GoogleSpeechConfig:
    """Validated Google STT settings loaded from the canonical Google config."""

    project_id: str
    location: str
    language_code: str
    model: str
    credential_json_path: Path | None
    # Optional proxy settings — when present, use external speech proxy instead of direct Google client
    proxy_url: str | None = None
    proxy_api_key: str | None = None
    config_path: Path

    @property
    def recognizer(self) -> str:
        return f"projects/{self.project_id}/locations/{self.location}/recognizers/_"

    @property
    def api_endpoint(self) -> str:
        return f"{self.location}-speech.googleapis.com"


def get_google_config_path(config_path: Path | None = None) -> Path:
    """Return the canonical Google STT config path next to the active app config."""
    return _resolve_google_config_path(config_path)


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
        return (config_path.parent / candidate).resolve()
    return candidate.resolve()


def _validate_project_local_path(path: Path, *, root: Path, label: str) -> None:
    root = root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GoogleConfigError(
            "Google credential file must stay inside the active config directory root: "
            f"{root}. Update {label} credentialJsonPath."
        ) from exc


def _validate_credential_file(path: Path) -> None:
    payload = _read_json_file(path, label="Google credential file")
    required_keys = ("type", "client_email", "private_key", "token_uri")
    missing = [key for key in required_keys if not str(payload.get(key) or "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise GoogleConfigError(f"Google credential file missing required field(s): {joined}")


def load_google_config(config_path: Path | None = None) -> GoogleSpeechConfig:
    """Load and validate the canonical Google config plus its credential file path."""
    path = get_google_config_path(config_path)
    legacy_path = _legacy_google_config_path(config_path)

    if legacy_path.exists():
        raise GoogleConfigError(
            "Unsupported legacy Google config file found: "
            f"{legacy_path}. Rename or move it to {path} and use google.example.json as the template."
        )
    # If a local google.json exists, prefer it. Otherwise allow using top-level config interview_proxy.
    if not path.exists():
        # Try to find interview proxy in main config.json
        try:
            from nanobot.config.loader import load_config

            cfg = load_config()
            ip = getattr(cfg, "interview_proxy", None)
            if ip and (getattr(ip, "base_url", None) or getattr(ip, "baseUrl", None)):
                # Build proxy-based GoogleSpeechConfig without local credential file
                proxy_url = str(getattr(ip, "base_url", "") or getattr(ip, "baseUrl", ""))
                proxy_api_key = str(getattr(ip, "api_key", "") or getattr(ip, "apiKey", ""))
                # Use some sensible defaults; project_id etc. may be blank when proxy handles everything
                return GoogleSpeechConfig(
                    project_id="",
                    location="",
                    language_code="",
                    model="",
                    credential_json_path=None,
                    proxy_url=proxy_url,
                    proxy_api_key=proxy_api_key,
                    config_path=path,
                )
        except Exception:
            pass
        raise GoogleConfigError(
            "Google config not found: "
            f"{path}. Create google.json from google.example.json next to {get_config_path().resolve().name}."
        )

    label = path.name
    payload = _read_json_file(path, label=label)

    project_id = payload.get("projectId", "")
    location = payload.get("location", "")
    language_code = payload.get("languageCode", "")
    model = payload.get("model", "")
    credential_json_path = payload.get("credentialJsonPath")

    # Validate if this is a local credential-based config
    if credential_json_path:
        project_id = _require_text(payload, "projectId", label=label)
        location = _require_text(payload, "location", label=label)
        language_code = _require_text(payload, "languageCode", label=label)
        model = _require_text(payload, "model", label=label)
        credential_json_path = _require_text(payload, "credentialJsonPath", label=label)

        if model != "chirp_3":
            raise GoogleConfigError(f"{label} field 'model' must be exactly 'chirp_3'")

        credential_path = _resolve_credential_path(path, credential_json_path)
        _validate_project_local_path(credential_path, root=path.parent, label=label)
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
            proxy_url=None,
            proxy_api_key=None,
            config_path=path,
        )

    # No local credential path — treat payload as minimal or fallback already handled
    return GoogleSpeechConfig(
        project_id=str(project_id or ""),
        location=str(location or ""),
        language_code=str(language_code or ""),
        model=str(model or ""),
        credential_json_path=None,
        proxy_url=None,
        proxy_api_key=None,
        config_path=path,
    )
