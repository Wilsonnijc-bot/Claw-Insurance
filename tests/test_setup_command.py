from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from nanobot.cli.commands import app
from nanobot.config.google_loader import load_google_config
from nanobot.config.loader import load_config

runner = CliRunner()


def _use_temp_project(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NANOBOT_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
    monkeypatch.delenv("NANOBOT_APP_CONFIG_PATH", raising=False)


def _write_google_credential(path: Path) -> None:
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


def test_setup_first_run_core_only_and_status(monkeypatch, tmp_path: Path) -> None:
    _use_temp_project(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        ["setup"],
        input="\n".join(
            [
                "3456",
                "litellm/kimi-k2.5",
                "http://43.129.246.127:4000",
                "sk-test-core",
                "n",
                "n",
            ]
        )
        + "\n",
    )

    assert result.exit_code == 0, result.stdout

    config_path = tmp_path / "config.json"
    assert config_path.exists()
    assert not (tmp_path / "supabaseconfig.json").exists()
    assert not (tmp_path / "googleconfig.json").exists()

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["providers"]["litellm"]["apiKey"] == "sk-test-core"
    assert payload["providers"]["litellm"]["baseUrl"] == "http://43.129.246.127:4000"
    assert payload["agents"]["defaults"]["provider"] == "litellm"
    assert payload["agents"]["defaults"]["model"] == "litellm/kimi-k2.5"
    assert payload["channels"]["whatsapp"]["enabled"] is True
    assert payload["channels"]["whatsapp"]["deliveryMode"] == "draft"
    assert "catalog" not in payload

    config = load_config()
    assert config.agents.defaults.provider == "litellm"
    assert config.agents.defaults.model == "litellm/kimi-k2.5"

    assert (tmp_path / "HEARTBEAT.md").exists()
    assert (tmp_path / "memory" / "GLOBAL.md").exists()
    assert (tmp_path / "media").exists()

    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0, status.stdout
    compact_status = status.stdout.replace("\n", "")
    assert str(tmp_path / "config.json").replace("\n", "") in compact_status
    assert str(tmp_path / "whatsapp-web").replace("\n", "") in compact_status
    assert "project-local" in status.stdout


def test_setup_supabase_only(monkeypatch, tmp_path: Path) -> None:
    _use_temp_project(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        ["setup"],
        input="\n".join(
            [
                "3456",
                "litellm/kimi-k2.5",
                "http://43.129.246.127:4000",
                "sk-test-supabase",
                "y",
                "",
                "service-role-key",
                "",
                "",
                "",
                "n",
            ]
        )
        + "\n",
    )

    assert result.exit_code == 0, result.stdout

    supabase_path = tmp_path / "supabaseconfig.json"
    google_path = tmp_path / "googleconfig.json"
    assert supabase_path.exists()
    assert not google_path.exists()

    payload = json.loads(supabase_path.read_text(encoding="utf-8"))
    assert payload["supabaseUrl"] == "https://znhoybdxcwlatsoqmbnt.supabase.co"
    assert payload["supabaseAnonKey"] == "service-role-key"
    assert payload["supabaseProjectRef"] == "znhoybdxcwlatsoqmbnt"
    assert payload["supabaseCatalogTables"] == ["insurance_products", "dental_insurance"]
    assert payload["autoRestorePausedProject"] is False

    config = load_config()
    assert config.catalog.supabase_url == "https://znhoybdxcwlatsoqmbnt.supabase.co"


def test_setup_google_only(monkeypatch, tmp_path: Path) -> None:
    _use_temp_project(monkeypatch, tmp_path)
    credential_path = tmp_path / "secrets" / "double-scholar-487115-b1-075776a1689b.json"
    _write_google_credential(credential_path)

    result = runner.invoke(
        app,
        ["setup"],
        input="\n".join(
            [
                "3456",
                "litellm/kimi-k2.5",
                "http://43.129.246.127:4000",
                "sk-litellm-test",
                "n",
                "y",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        + "\n",
    )

    assert result.exit_code == 0, result.stdout

    google_path = tmp_path / "googleconfig.json"
    supabase_path = tmp_path / "supabaseconfig.json"
    assert google_path.exists()
    assert not supabase_path.exists()

    google = load_google_config(google_path)
    assert google.project_id == "double-scholar-487115-b1"
    assert google.model == "chirp_3"
    assert google.credential_json_path == credential_path.resolve()


def test_setup_both_enabled(monkeypatch, tmp_path: Path) -> None:
    _use_temp_project(monkeypatch, tmp_path)
    _write_google_credential(tmp_path / "secrets" / "double-scholar-487115-b1-075776a1689b.json")

    result = runner.invoke(
        app,
        ["setup"],
        input="\n".join(
            [
                "3456",
                "litellm/kimi-k2.5",
                "http://43.129.246.127:4000",
                "sk-litellm-test",
                "y",
                "",
                "service-role-key",
                "",
                "",
                "",
                "y",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        + "\n",
    )

    assert result.exit_code == 0, result.stdout
    assert (tmp_path / "config.json").exists()
    assert (tmp_path / "supabaseconfig.json").exists()
    assert (tmp_path / "googleconfig.json").exists()

    config = load_config()
    assert config.agents.defaults.provider == "litellm"
    assert config.catalog.supabase_project_ref == "znhoybdxcwlatsoqmbnt"

    google = load_google_config(tmp_path / "googleconfig.json")
    assert google.project_id == "double-scholar-487115-b1"


def test_setup_rerun_handles_update_skip_and_overwrite(monkeypatch, tmp_path: Path) -> None:
    _use_temp_project(monkeypatch, tmp_path)
    _write_google_credential(tmp_path / "secrets" / "old-google.json")
    _write_google_credential(tmp_path / "secrets" / "new-google.json")

    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "agents": {
                    "defaults": {
                        "provider": "litellm",
                        "model": "old-model"
                    }
                },
                "providers": {
                    "litellm": {
                        "apiKey": "old-key",
                        "baseUrl": "http://old.example/v1",
                    }
                },
                "tools": {
                    "exec": {
                        "timeout": 123,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "supabaseconfig.json").write_text(
        json.dumps(
            {
                "supabaseUrl": "https://old.supabase.co",
                "supabaseAnonKey": "old-supabase-key",
                "supabaseCatalogTables": ["legacy_table"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "googleconfig.json").write_text(
        json.dumps(
            {
                "projectId": "old-project",
                "location": "us",
                "languageCode": "yue-Hant-HK",
                "model": "chirp_3",
                "credentialJsonPath": "secrets/old-google.json",
                "extraNote": "keep-me",
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["setup"],
        input="\n".join(
            [
                "4567",
                "litellm/kimi-k2.5",
                "https://new.example/v1",
                "new-key",
                "y",
                "https://new.supabase.co",
                "new-supabase-key",
                "new-ref",
                "insurance_products,dental_insurance",
                "",
                "y",
                "new-project",
                "eu",
                "yue-Hant-HK",
                "chirp_3",
                "secrets/new-google.json",
                "u",
                "s",
                "o",
            ]
        )
        + "\n",
    )

    assert result.exit_code == 0, result.stdout

    config_payload = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    supabase_payload = json.loads((tmp_path / "supabaseconfig.json").read_text(encoding="utf-8"))
    google_payload = json.loads((tmp_path / "googleconfig.json").read_text(encoding="utf-8"))

    assert config_payload["providers"]["litellm"]["apiKey"] == "new-key"
    assert config_payload["providers"]["litellm"]["baseUrl"] == "https://new.example/v1"
    assert config_payload["agents"]["defaults"]["provider"] == "litellm"
    assert config_payload["agents"]["defaults"]["model"] == "litellm/kimi-k2.5"
    assert config_payload["channels"]["whatsapp"]["deliveryMode"] == "draft"
    assert config_payload["tools"]["exec"]["timeout"] == 123

    config = load_config()
    assert config.agents.defaults.provider == "litellm"
    assert config.agents.defaults.model == "litellm/kimi-k2.5"

    assert supabase_payload["supabaseUrl"] == "https://old.supabase.co"
    assert supabase_payload["supabaseCatalogTables"] == ["legacy_table"]

    assert google_payload["projectId"] == "new-project"
    assert google_payload["location"] == "eu"
    assert google_payload["credentialJsonPath"] == "secrets/new-google.json"
    assert "extraNote" not in google_payload
