from __future__ import annotations

from dataclasses import dataclass

import pytest

from nanobot.config.schema import Config
from nanobot.insurance_catalog import (
    CatalogSettings,
    CatalogUnavailableError,
    SupabaseCatalogRepository,
    clear_catalog_cache,
    load_catalog_settings,
)


@dataclass
class _FakeResponse:
    status_code: int
    payload: list[dict] | dict
    text: str = ""

    def json(self):
        return self.payload


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def get(self, url, *args, **kwargs):
        self.calls.append(("GET", str(url)))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, *args, **kwargs):
        self.calls.append(("POST", str(url)))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_catalog_config_accepts_supabase_fields() -> None:
    config = Config.model_validate(
        {
            "catalog": {
                "supabaseUrl": "https://example.supabase.co",
                "supabaseAnonKey": "anon-key",
                "supabaseProjectRef": "example",
                "supabaseManagementToken": "sbp-token",
                "supabaseCatalogTable": "insurance_products",
                "supabaseCatalogTables": ["insurance_products", "dental_insurance"],
                "autoRestorePausedProject": True,
                "restoreTimeoutSeconds": 120,
                "cacheTtlSeconds": 120,
            }
        }
    )

    assert config.catalog.supabase_url == "https://example.supabase.co"
    assert config.catalog.supabase_anon_key == "anon-key"
    assert config.catalog.supabase_project_ref == "example"
    assert config.catalog.supabase_management_token == "sbp-token"
    assert config.catalog.supabase_catalog_table == "insurance_products"
    assert config.catalog.supabase_catalog_tables == ["insurance_products", "dental_insurance"]
    assert config.catalog.auto_restore_paused_project is True
    assert config.catalog.restore_timeout_seconds == 120
    assert config.catalog.cache_ttl_seconds == 120


def test_supabase_repository_uses_warm_cache_on_fetch_failure() -> None:
    clear_catalog_cache()
    settings = CatalogSettings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_catalog_table="insurance_products",
        supabase_catalog_tables=("insurance_products",),
        cache_ttl_seconds=60,
    )
    success_rows = [
        {
            "plan_id": "p1",
            "plan_name": "Plan 1",
            "provider_company": "AIA",
            "plan_category": "dental",
            "coverage_description": "Basic cover",
            "pricing": "HK$100/year",
            "age": "18-60",
            "customer_requirement": "Hong Kong residents",
            "price_structure": "annual",
            "additional_informations": "",
            "product_brochure_route": "https://example.com/plan1.pdf",
            "url": "https://example.com/plan1",
        }
    ]
    warm_repo = SupabaseCatalogRepository(
        settings,
        client_factory=lambda **kwargs: _FakeClient([_FakeResponse(200, success_rows)]),
    )
    fallback_repo = SupabaseCatalogRepository(
        settings,
        client_factory=lambda **kwargs: _FakeClient([RuntimeError("network down")]),
    )

    warm_rows = warm_repo.get_rows()
    fallback_rows = fallback_repo.get_rows()

    assert warm_rows == fallback_rows
    assert fallback_rows[0]["source_file"] == "supabase"


def test_supabase_repository_fails_cleanly_when_cache_is_cold() -> None:
    clear_catalog_cache()
    settings = CatalogSettings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_catalog_table="insurance_products",
        supabase_catalog_tables=("insurance_products",),
        cache_ttl_seconds=60,
    )
    repo = SupabaseCatalogRepository(
        settings,
        client_factory=lambda **kwargs: _FakeClient([RuntimeError("network down")]),
    )

    with pytest.raises(CatalogUnavailableError):
        repo.get_rows()


def test_supabase_repository_reads_both_raw_tables() -> None:
    clear_catalog_cache()
    settings = CatalogSettings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_catalog_tables=("insurance_products", "dental_insurance"),
        cache_ttl_seconds=60,
    )
    repo = SupabaseCatalogRepository(
        settings,
        client_factory=lambda **kwargs: _FakeClient(
            [
                _FakeResponse(
                    200,
                    [
                        {
                            "plan_id": "p1",
                            "plan_name": "Plan 1",
                            "provider_company": "AIA",
                            "plan_category": "health",
                        }
                    ],
                ),
                _FakeResponse(
                    200,
                    [
                        {
                            "plan_id": "d1",
                            "plan_name": "Dental 1",
                            "provider_company": "Bupa",
                            "plan_category": "dental",
                        }
                    ],
                ),
            ]
        ),
    )

    rows = repo.get_rows()

    assert [row["plan_id"] for row in rows] == ["p1", "d1"]
    assert all(row["source_file"] == "supabase" for row in rows)


def test_supabase_repository_restores_inactive_project_and_retries() -> None:
    clear_catalog_cache()
    settings = CatalogSettings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_project_ref="proj-ref",
        supabase_management_token="sbp-token",
        supabase_catalog_tables=("insurance_products",),
        restore_timeout_seconds=1,
        cache_ttl_seconds=60,
    )
    client = _FakeClient(
        [
            _FakeResponse(540, {"message": "Project is paused"}, text="Project is paused"),
            _FakeResponse(200, {"status": "INACTIVE"}),
            _FakeResponse(202, {"status": "RESTORING"}),
            _FakeResponse(200, {"status": "ACTIVE_HEALTHY"}),
            _FakeResponse(
                200,
                [
                    {
                        "plan_id": "p1",
                        "plan_name": "Plan 1",
                        "provider_company": "AIA",
                        "plan_category": "dental",
                    }
                ],
            ),
        ]
    )
    repo = SupabaseCatalogRepository(
        settings,
        client_factory=lambda **kwargs: client,
    )

    rows = repo.get_rows()

    assert [row["plan_id"] for row in rows] == ["p1"]
    assert ("POST", "https://api.supabase.com/v1/projects/proj-ref/restore") in client.calls


def test_supabase_repository_inactive_project_fails_cleanly_without_management_token() -> None:
    clear_catalog_cache()
    settings = CatalogSettings(
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon-key",
        supabase_catalog_tables=("insurance_products",),
        cache_ttl_seconds=60,
    )
    repo = SupabaseCatalogRepository(
        settings,
        client_factory=lambda **kwargs: _FakeClient(
            [_FakeResponse(540, {"message": "Project is paused"}, text="Project is paused")]
        ),
    )

    with pytest.raises(CatalogUnavailableError, match="missing supabase_management_token"):
        repo.get_rows()


def test_load_catalog_settings_prefers_env_over_split_and_legacy(monkeypatch, tmp_path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    app_config = app_dir / "nanobot.json"
    app_config.write_text(
        """
        {
          "catalog": {
            "supabaseUrl": "https://legacy.supabase.co",
            "supabaseAnonKey": "legacy-key",
            "supabaseCatalogTables": ["legacy_table"]
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    (app_dir / "supabaseconfig.json").write_text(
        """
        {
          "supabaseUrl": "https://split.supabase.co",
          "supabaseAnonKey": "split-key",
          "supabaseCatalogTables": ["split_table"]
        }
        """.strip(),
        encoding="utf-8",
    )

    monkeypatch.setenv("NANOBOT_APP_CONFIG_PATH", str(app_config))
    monkeypatch.setenv("SUPABASE_URL", "https://env.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "env-key")
    monkeypatch.setenv("SUPABASE_CATALOG_TABLES", "env_table_a,env_table_b")

    settings = load_catalog_settings()

    assert settings.supabase_url == "https://env.supabase.co"
    assert settings.supabase_anon_key == "env-key"
    assert settings.supabase_catalog_tables == ("env_table_a", "env_table_b")
