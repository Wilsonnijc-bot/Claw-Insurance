from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.config.google_loader import (
    GOOGLE_CONFIG_FILENAME,
    GoogleConfigError,
    get_google_config_path,
    load_google_config,
)


def _write_valid_credential(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "client_email": "nanobot@example.iam.gserviceaccount.com",
                "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        ),
        encoding="utf-8",
    )


def _write_google_config(
    path: Path,
    *,
    credential_json_path: str = "secrets/google-credentials.json",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "projectId": "demo-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "chirp_3",
                "credentialJsonPath": credential_json_path,
            }
        ),
        encoding="utf-8",
    )


def test_get_google_config_path_returns_canonical_path_next_to_main_config(tmp_path: Path) -> None:
    config_path = tmp_path / "app" / "nanobot.json"

    assert get_google_config_path(config_path) == (tmp_path / "app" / GOOGLE_CONFIG_FILENAME)


def test_load_google_config_rejects_explicit_legacy_filename(tmp_path: Path) -> None:
    legacy_path = tmp_path / "googleconfig.json"
    _write_google_config(legacy_path)

    with pytest.raises(GoogleConfigError, match="Unsupported legacy Google config file found"):
        load_google_config(legacy_path)


def test_load_google_config_rejects_legacy_file_next_to_active_config(tmp_path: Path, monkeypatch) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")
    _write_google_config(app_dir / "googleconfig.json")

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    with pytest.raises(GoogleConfigError, match="Unsupported legacy Google config file found"):
        load_google_config()


def test_load_google_config_requires_canonical_file_and_mentions_example(
    tmp_path: Path,
    monkeypatch,
) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    with pytest.raises(GoogleConfigError) as exc_info:
        load_google_config()

    message = str(exc_info.value)
    assert f"Google config not found: {app_dir / 'google.json'}" in message
    assert "google.example.json" in message


def test_load_google_config_requires_credential_path(tmp_path: Path) -> None:
    config_path = tmp_path / "google.json"
    config_path.write_text(
        json.dumps(
            {
                "projectId": "demo-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "chirp_3",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoogleConfigError, match="credentialJsonPath"):
        load_google_config(config_path)


def test_load_google_config_uses_google_json_next_to_active_config(tmp_path: Path, monkeypatch) -> None:
    app_dir = tmp_path / "app"
    app_config = app_dir / "nanobot.json"
    app_config.parent.mkdir(parents=True, exist_ok=True)
    app_config.write_text("{}", encoding="utf-8")
    credential_path = app_dir / "secrets" / "google-credentials.json"
    _write_valid_credential(credential_path)
    _write_google_config(app_dir / "google.json")

    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))

    config = load_google_config()

    assert config.config_path == (app_dir / "google.json").resolve()
    assert config.credential_json_path == credential_path.resolve()


def test_load_google_config_accepts_explicit_google_json_path(tmp_path: Path) -> None:
    config_path = tmp_path / "google.json"
    credential_path = tmp_path / "secrets" / "google-credentials.json"
    _write_valid_credential(credential_path)
    _write_google_config(config_path)

    config = load_google_config(config_path)

    assert config.project_id == "demo-project"
    assert config.location == "us"
    assert config.language_code == "yue-Hant-HK"
    assert config.model == "chirp_3"
    assert config.credential_json_path == credential_path.resolve()
    assert config.api_endpoint == "us-speech.googleapis.com"
    assert config.recognizer == "projects/demo-project/locations/us/recognizers/_"


def test_load_google_config_rejects_wrong_model(tmp_path: Path) -> None:
    config_path = tmp_path / "google.json"
    credential_path = tmp_path / "secrets" / "google-credentials.json"
    _write_valid_credential(credential_path)
    config_path.write_text(
        json.dumps(
            {
                "projectId": "demo-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "latest_long",
                "credentialJsonPath": "secrets/google-credentials.json",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoogleConfigError, match="chirp_3"):
        load_google_config(config_path)


def test_load_google_config_requires_existing_credential_file(tmp_path: Path) -> None:
    config_path = tmp_path / "google.json"
    _write_google_config(config_path)

    with pytest.raises(GoogleConfigError, match="Google credential file not found"):
        load_google_config(config_path)


def test_load_google_config_rejects_credential_path_outside_active_config_dir(tmp_path: Path) -> None:
    config_path = tmp_path / "google.json"
    external_credential_path = tmp_path.parent / "google-credentials.json"
    _write_valid_credential(external_credential_path)
    _write_google_config(config_path, credential_json_path=str(external_credential_path))

    with pytest.raises(GoogleConfigError, match="active config directory root"):
        load_google_config(config_path)
