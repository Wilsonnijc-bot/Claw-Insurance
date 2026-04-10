from __future__ import annotations

import json
from pathlib import Path

import pytest

from nanobot.config.google_loader import GoogleConfigError, load_google_config


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


def test_load_google_config_requires_file(tmp_path: Path) -> None:
    with pytest.raises(GoogleConfigError, match="googleconfig.json not found"):
        load_google_config(tmp_path / "googleconfig.json")


def test_load_google_config_requires_credential_path(tmp_path: Path) -> None:
    config_path = tmp_path / "googleconfig.json"
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


def test_load_google_config_requires_existing_credential_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nanobot.config.google_loader.project_root", lambda: tmp_path)
    config_path = tmp_path / "googleconfig.json"
    config_path.write_text(
        json.dumps(
            {
                "projectId": "demo-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "chirp_3",
                "credentialJsonPath": "secrets/google-credentials.json",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoogleConfigError, match="Google credential file not found"):
        load_google_config(config_path)


def test_load_google_config_rejects_wrong_model(tmp_path: Path) -> None:
    config_path = tmp_path / "googleconfig.json"
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


def test_load_google_config_accepts_relative_credential_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nanobot.config.google_loader.project_root", lambda: tmp_path)
    config_path = tmp_path / "googleconfig.json"
    credential_path = tmp_path / "secrets" / "google-credentials.json"
    _write_valid_credential(credential_path)
    config_path.write_text(
        json.dumps(
            {
                "projectId": "demo-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "chirp_3",
                "credentialJsonPath": "secrets/google-credentials.json",
            }
        ),
        encoding="utf-8",
    )

    config = load_google_config(config_path)

    assert config.project_id == "demo-project"
    assert config.location == "us"
    assert config.language_code == "yue-Hant-HK"
    assert config.model == "chirp_3"
    assert config.credential_json_path == credential_path.resolve()
    assert config.api_endpoint == "us-speech.googleapis.com"
    assert config.recognizer == "projects/demo-project/locations/us/recognizers/_"


def test_load_google_config_rejects_credential_path_outside_project_root(tmp_path: Path) -> None:
    config_path = tmp_path / "googleconfig.json"
    external_credential_path = tmp_path / "google-credentials.json"
    _write_valid_credential(external_credential_path)
    config_path.write_text(
        json.dumps(
            {
                "projectId": "demo-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "chirp_3",
                "credentialJsonPath": str(external_credential_path),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(GoogleConfigError, match="must stay inside this project root"):
        load_google_config(config_path)
