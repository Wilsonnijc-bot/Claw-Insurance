from __future__ import annotations

import csv
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.parse import quote, urlparse

import httpx

from nanobot.config.loader import load_config


_CANONICAL_FIELDS = (
    "plan_id",
    "plan_name",
    "provider_company",
    "plan_category",
    "coverage_description",
    "pricing",
    "age",
    "customer_requirement",
    "price_structure",
    "additional_informations",
    "product_brochure_route",
    "url",
)

_DEFAULT_CACHE_TTL_SECONDS = 300
_DEFAULT_PAGE_SIZE = 1000
_DEFAULT_RESTORE_TIMEOUT_SECONDS = 300
_DEFAULT_RESTORE_POLL_SECONDS = 5
_SUPABASE_MANAGEMENT_API_BASE = "https://api.supabase.com/v1"


def normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_row(mapping: dict[str, Any], *, source_file: str) -> dict[str, Any]:
    normalized = {
        normalize_header(str(key)): normalize_text(value)
        for key, value in mapping.items()
    }
    normalized.setdefault("additional_informations", normalized.get("additional_info", ""))
    row = {field: normalized.get(field, "") for field in _CANONICAL_FIELDS}
    row["source_file"] = source_file
    return row


@dataclass(frozen=True)
class CatalogSettings:
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_project_ref: str = ""
    supabase_management_token: str = ""
    supabase_catalog_table: str = ""
    supabase_catalog_tables: tuple[str, ...] = ()
    # Optional DB proxy settings (when present, use proxy instead of direct Supabase requests)
    db_proxy_url: str = ""
    db_proxy_api_key: str = ""
    auto_restore_paused_project: bool = True
    restore_timeout_seconds: int = _DEFAULT_RESTORE_TIMEOUT_SECONDS
    cache_ttl_seconds: int = _DEFAULT_CACHE_TTL_SECONDS


@dataclass
class _CacheEntry:
    rows: list[dict[str, Any]]
    expires_at: float


class CatalogUnavailableError(RuntimeError):
    """Raised when the product catalog cannot be loaded."""


class CatalogRepository(Protocol):
    def get_rows(self) -> list[dict[str, Any]]:
        """Return normalized catalog rows."""


_CACHE: dict[tuple[str, str], _CacheEntry] = {}


def clear_catalog_cache() -> None:
    _CACHE.clear()


def load_catalog_settings() -> CatalogSettings:
    config = load_config()
    configured = config.catalog.model_dump() if getattr(config, "catalog", None) else {}

    def _pick(*keys: str, default: str = "") -> str:
        for key in keys:
            value = os.environ.get(key)
            if value:
                return value.strip()
        return str(configured.get(default, "")).strip() if default else ""

    def _pick_bool(*keys: str, default: str = "", fallback: bool = False) -> bool:
        for key in keys:
            raw = os.environ.get(key)
            if raw is not None:
                return str(raw).strip().casefold() not in {"", "0", "false", "no", "off"}
        raw = configured.get(default, fallback) if default else fallback
        if isinstance(raw, bool):
            return raw
        if raw is None:
            return fallback
        return str(raw).strip().casefold() not in {"", "0", "false", "no", "off"}

    raw_ttl = (
        os.environ.get("CATALOG__CACHE_TTL_SECONDS")
        or os.environ.get("SUPABASE_CACHE_TTL_SECONDS")
        or configured.get("cache_ttl_seconds", _DEFAULT_CACHE_TTL_SECONDS)
    )
    try:
        ttl = max(int(raw_ttl), 0)
    except (TypeError, ValueError):
        ttl = _DEFAULT_CACHE_TTL_SECONDS

    raw_restore_timeout = (
        os.environ.get("CATALOG__RESTORE_TIMEOUT_SECONDS")
        or os.environ.get("SUPABASE_RESTORE_TIMEOUT_SECONDS")
        or configured.get("restore_timeout_seconds", _DEFAULT_RESTORE_TIMEOUT_SECONDS)
    )
    try:
        restore_timeout = max(int(raw_restore_timeout), 0)
    except (TypeError, ValueError):
        restore_timeout = _DEFAULT_RESTORE_TIMEOUT_SECONDS

    raw_tables = (
        os.environ.get("CATALOG__SUPABASE_CATALOG_TABLES")
        or os.environ.get("SUPABASE_CATALOG_TABLES")
        or configured.get("supabase_catalog_tables", [])
    )
    tables: list[str]
    if isinstance(raw_tables, str):
        tables = [item.strip() for item in raw_tables.split(",") if item.strip()]
    elif isinstance(raw_tables, list):
        tables = [str(item).strip() for item in raw_tables if str(item).strip()]
    else:
        tables = []

    single_table = _pick(
        "CATALOG__SUPABASE_CATALOG_TABLE",
        "SUPABASE_CATALOG_TABLE",
        default="supabase_catalog_table",
    )
    if single_table:
        tables = [single_table]
    if not tables:
        tables = ["insurance_products", "dental_insurance"]

    # DB proxy config may appear under catalog.db_proxy (camelCase) or catalog.db_proxy
    db_proxy_conf = configured.get("db_proxy") or configured.get("dbProxy") or {}
    db_proxy_url = (
        os.environ.get("CATALOG__DB_PROXY_URL")
        or os.environ.get("DB_PROXY_URL")
        or str(db_proxy_conf.get("baseUrl") or db_proxy_conf.get("base_url") or "")
    ).strip()
    db_proxy_api_key = (
        os.environ.get("CATALOG__DB_PROXY_API_KEY")
        or os.environ.get("DB_PROXY_API_KEY")
        or str(db_proxy_conf.get("apiKey") or db_proxy_conf.get("api_key") or "")
    ).strip()

    return CatalogSettings(
        supabase_url=_pick("CATALOG__SUPABASE_URL", "SUPABASE_URL", default="supabase_url"),
        supabase_anon_key=_pick(
            "CATALOG__SUPABASE_ANON_KEY",
            "SUPABASE_ANON_KEY",
            "SUPABASE_KEY",
            default="supabase_anon_key",
        ),
        supabase_project_ref=_pick(
            "CATALOG__SUPABASE_PROJECT_REF",
            "SUPABASE_PROJECT_REF",
            default="supabase_project_ref",
        ),
        supabase_management_token=_pick(
            "CATALOG__SUPABASE_MANAGEMENT_TOKEN",
            "SUPABASE_MANAGEMENT_TOKEN",
            "SUPABASE_ACCESS_TOKEN",
            default="supabase_management_token",
        ),
        supabase_catalog_table=single_table,
        supabase_catalog_tables=tuple(tables),
        db_proxy_url=db_proxy_url,
        db_proxy_api_key=db_proxy_api_key,
        auto_restore_paused_project=_pick_bool(
            "CATALOG__AUTO_RESTORE_PAUSED_PROJECT",
            "SUPABASE_AUTO_RESTORE_PAUSED_PROJECT",
            default="auto_restore_paused_project",
            fallback=True,
        ),
        restore_timeout_seconds=restore_timeout,
        cache_ttl_seconds=ttl,
    )


def get_default_catalog_repository() -> CatalogRepository:
    return SupabaseCatalogRepository(load_catalog_settings())


class StaticCatalogRepository:
    """Simple in-memory repository for tests."""

    def __init__(self, rows: list[dict[str, Any]], source_file: str = "supabase") -> None:
        self._rows = [_normalize_row(row, source_file=source_file) for row in rows]

    def get_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]


class CsvCatalogRepository:
    """CSV-backed repository used only for tests/dev overrides."""

    def __init__(self, paths: list[Path]) -> None:
        self._paths = list(paths)

    def get_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in self._paths:
            with path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                for raw in reader:
                    rows.append(_normalize_row(raw, source_file=path.name))
        return rows

    def _fetch_table_rows_via_proxy(
        self,
        client: httpx.Client,
        settings: CatalogSettings,
        table_name: str,
    ) -> list[dict[str, Any]]:
        """Fetch table rows by calling an external DB proxy service."""
        base = settings.db_proxy_url.rstrip("/")
        url = f"{base}/query"
        rows: list[dict[str, Any]] = []
        offset = 0
        headers = {"Accept": "application/json"}
        if settings.db_proxy_api_key:
            headers["Authorization"] = f"Bearer {settings.db_proxy_api_key}"

        while True:
            payload = {
                "query_type": "select",
                "table": table_name,
                "limit": self._page_size,
                "offset": offset,
            }
            response = client.post(url, json=payload, headers=headers)
            if response.status_code >= 400:
                detail = response.text.strip()[:300]
                raise CatalogUnavailableError(
                    f"DB proxy request failed for {table_name} ({response.status_code}): {detail}"
                )
            try:
                payload_json = response.json()
            except Exception:
                raise CatalogUnavailableError("DB proxy returned non-JSON payload.")

            # Accept either a top-level list or an object with 'rows' list
            if isinstance(payload_json, list):
                batch_src = payload_json
            elif isinstance(payload_json, dict) and isinstance(payload_json.get("rows"), list):
                batch_src = payload_json.get("rows")
            else:
                raise CatalogUnavailableError(
                    f"DB proxy returned unexpected payload shape for {table_name}."
                )

            batch = [
                _normalize_row(item, source_file="supabase")
                for item in batch_src
                if isinstance(item, dict)
            ]
            rows.extend(batch)
            if len(batch_src) < self._page_size:
                break
            offset += len(batch_src)
        return rows


class SupabaseCatalogRepository:
    """Read-only Supabase-backed product catalog with a small in-process cache."""

    def __init__(
        self,
        settings: CatalogSettings,
        *,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
        page_size: int = _DEFAULT_PAGE_SIZE,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._page_size = max(page_size, 1)

    def get_rows(self) -> list[dict[str, Any]]:
        cache_key = (
            self._settings.supabase_url.rstrip("/"),
            ",".join(self._table_names()),
        )
        now = time.monotonic()
        cached = _CACHE.get(cache_key)
        if cached and cached.expires_at > now:
            return [dict(row) for row in cached.rows]

        try:
            rows = self._fetch_rows()
        except CatalogUnavailableError:
            if cached:
                return [dict(row) for row in cached.rows]
            raise

        _CACHE[cache_key] = _CacheEntry(
            rows=[dict(row) for row in rows],
            expires_at=now + max(self._settings.cache_ttl_seconds, 0),
        )
        return [dict(row) for row in rows]

    def _fetch_rows(self) -> list[dict[str, Any]]:
        settings = self._settings
        if not settings.supabase_url:
            # If DB proxy is configured, we will use it instead of direct Supabase access
            if not settings.db_proxy_url:
                raise CatalogUnavailableError("Supabase catalog is not configured: missing supabase_url.")
        if not settings.supabase_anon_key:
            # when using db proxy, anon key is not required
            if not settings.db_proxy_url:
                raise CatalogUnavailableError("Supabase catalog is not configured: missing supabase_anon_key.")
        table_names = self._table_names()
        if not table_names:
            raise CatalogUnavailableError(
                "Supabase catalog is not configured: missing supabase catalog table names."
            )

        headers = {
            "apikey": settings.supabase_anon_key,
            "Authorization": f"Bearer {settings.supabase_anon_key}",
            "Accept": "application/json",
        }

        rows: list[dict[str, Any]] = []
        try:
            with self._client_factory(timeout=10.0) as client:
                attempted_recovery = False
                while True:
                    rows = []
                    try:
                        for table_name in table_names:
                            if settings.db_proxy_url:
                                rows.extend(self._fetch_table_rows_via_proxy(client, settings, table_name))
                            else:
                                rows.extend(self._fetch_table_rows(client, settings.supabase_url, table_name, headers))
                        break
                    except CatalogUnavailableError as exc:
                        if attempted_recovery or not self._should_attempt_restore(str(exc)):
                            raise
                        if not self._reactivate_project_if_needed(client, str(exc)):
                            raise
                        attempted_recovery = True
        except CatalogUnavailableError:
            raise
        except Exception as exc:
            raise CatalogUnavailableError(f"Supabase catalog request failed: {exc}") from exc

        return rows

    def _project_ref(self) -> str:
        explicit = self._settings.supabase_project_ref.strip()
        if explicit:
            return explicit
        hostname = (urlparse(self._settings.supabase_url).hostname or "").strip().lower()
        match = re.match(r"^([a-z0-9-]+)\.supabase\.[a-z.]+$", hostname)
        return match.group(1) if match else ""

    @staticmethod
    def _project_status_kind(status: str | None) -> str:
        lowered = normalize_text(status).casefold()
        if not lowered:
            return "unknown"
        if any(token in lowered for token in ("inactive", "paused", "suspended", "retired")):
            return "inactive"
        if any(token in lowered for token in ("restore", "starting", "resum", "provision", "pending")):
            return "restoring"
        if "active" in lowered or "healthy" in lowered or "running" in lowered:
            return "active"
        return "unknown"

    @staticmethod
    def _should_attempt_restore(detail: str) -> bool:
        lowered = normalize_text(detail).casefold()
        return any(
            token in lowered
            for token in (
                " 540",
                "(540)",
                "inactive",
                "paused",
                "restore project",
                "temporarily retired",
                "retired",
            )
        )

    def _management_headers(self) -> dict[str, str]:
        token = self._settings.supabase_management_token.strip()
        if not token:
            raise CatalogUnavailableError(
                "Supabase project appears inactive, but automatic restore is unavailable: "
                "missing supabase_management_token."
            )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def _project_management_url(self) -> str:
        project_ref = self._project_ref()
        if not project_ref:
            raise CatalogUnavailableError(
                "Supabase project appears inactive, but automatic restore is unavailable: "
                "missing supabase_project_ref and it could not be inferred from supabase_url."
            )
        return f"{_SUPABASE_MANAGEMENT_API_BASE}/projects/{quote(project_ref, safe='')}"

    def _reactivate_project_if_needed(self, client: httpx.Client, detail: str) -> bool:
        if not self._settings.auto_restore_paused_project:
            raise CatalogUnavailableError(
                f"{detail} Automatic restore is disabled for this catalog configuration."
            )

        headers = self._management_headers()
        project_url = self._project_management_url()
        status = self._get_project_status(client, project_url, headers)
        status_kind = self._project_status_kind(status)

        if status_kind == "active":
            return False

        if status_kind == "inactive":
            restore_response = client.post(f"{project_url}/restore", headers=headers)
            if restore_response.status_code >= 400:
                detail = restore_response.text.strip()[:300]
                raise CatalogUnavailableError(
                    f"Supabase project restore failed ({restore_response.status_code}): {detail}"
                )

        if status_kind in {"inactive", "restoring"}:
            self._wait_for_project_active(client, project_url, headers)
            return True

        return False

    def _get_project_status(
        self,
        client: httpx.Client,
        project_url: str,
        headers: dict[str, str],
    ) -> str:
        response = client.get(project_url, headers=headers)
        if response.status_code >= 400:
            detail = response.text.strip()[:300]
            raise CatalogUnavailableError(
                f"Supabase project status request failed ({response.status_code}): {detail}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise CatalogUnavailableError("Supabase project status request returned a non-object payload.")
        return normalize_text(payload.get("status", ""))

    def _wait_for_project_active(
        self,
        client: httpx.Client,
        project_url: str,
        headers: dict[str, str],
    ) -> None:
        timeout_seconds = max(int(self._settings.restore_timeout_seconds), 0)
        deadline = time.monotonic() + timeout_seconds
        last_status = ""

        while True:
            last_status = self._get_project_status(client, project_url, headers)
            if self._project_status_kind(last_status) == "active":
                return
            if timeout_seconds == 0 or time.monotonic() >= deadline:
                raise CatalogUnavailableError(
                    "Supabase project restore did not become active before timeout. "
                    f"Last known status: {last_status or 'unknown'}."
                )
            time.sleep(_DEFAULT_RESTORE_POLL_SECONDS)

    def _table_names(self) -> tuple[str, ...]:
        if self._settings.supabase_catalog_tables:
            return tuple(item.strip() for item in self._settings.supabase_catalog_tables if item.strip())
        if self._settings.supabase_catalog_table.strip():
            return (self._settings.supabase_catalog_table.strip(),)
        return ()

    def _fetch_table_rows(
        self,
        client: httpx.Client,
        supabase_url: str,
        table_name: str,
        headers: dict[str, str],
    ) -> list[dict[str, Any]]:
        url = supabase_url.rstrip("/") + "/rest/v1/" + quote(table_name.strip(), safe=".")
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = client.get(
                url,
                params={
                    "select": "*",
                    "limit": self._page_size,
                    "offset": offset,
                },
                headers=headers,
            )
            if response.status_code >= 400:
                detail = response.text.strip()[:300]
                raise CatalogUnavailableError(
                    f"Supabase catalog request failed for {table_name} ({response.status_code}): {detail}"
                )
            payload = response.json()
            if not isinstance(payload, list):
                raise CatalogUnavailableError(
                    f"Supabase catalog returned a non-list payload for {table_name}."
                )
            batch = [
                _normalize_row(item, source_file="supabase")
                for item in payload
                if isinstance(item, dict)
            ]
            rows.extend(batch)
            if len(payload) < self._page_size:
                break
            offset += len(payload)
        return rows
