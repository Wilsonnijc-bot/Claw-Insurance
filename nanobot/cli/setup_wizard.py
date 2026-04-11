"""Guided interactive setup for split Nanobot config files."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import typer
from rich.console import Console

from nanobot import __logo__
from nanobot.config.google_loader import (
    GoogleConfigError,
    get_google_config_path,
    load_google_config,
)
from nanobot.config.loader import get_config_path, load_config
from nanobot.config.schema import Config
from nanobot.config.supabase_loader import (
    get_supabase_config_path,
    load_supabase_config,
)
from nanobot.utils.helpers import get_workspace_path, sync_workspace_templates
from nanobot.utils.paths import confine_path, ensure_runtime_dirs, project_root, resolve_project_relative

console = Console()

_DEFAULT_LITELLM_MODEL = "litellm/kimi-k2.5"
_DEFAULT_LITELLM_BASE_URL = "http://43.129.246.127:4000"
_DEFAULT_SUPABASE_TABLES = ["insurance_products", "dental_insurance"]


def run_setup_wizard() -> None:
    """Run the guided setup flow and write split config files."""
    config_path = get_config_path()
    supabase_path = get_supabase_config_path(config_path)
    google_path = get_google_config_path()

    existing_config_raw = _read_json_object(config_path)
    existing_supabase_raw = _read_json_object(supabase_path)
    existing_google_raw = _read_json_object(google_path)

    try:
        existing_config = load_config(config_path) if config_path.exists() else Config()
    except Exception:
        existing_config = Config()

    try:
        existing_catalog = load_supabase_config(config_path) if supabase_path.exists() else {}
    except Exception:
        existing_catalog = {}

    console.print(f"{__logo__} nanobot setup\n")
    console.print("This guided setup keeps Nanobot's split config layout:")
    console.print(f"  [cyan]{config_path.name}[/cyan] for core runtime settings")
    console.print(f"  [cyan]{supabase_path.name}[/cyan] for Supabase catalog settings")
    console.print(f"  [cyan]{google_path.name}[/cyan] for Google STT settings")
    console.print("\nNothing will be overwritten silently.\n")

    core_answers = _prompt_core_config(
        existing_config,
        has_existing_config=config_path.exists(),
    )
    wants_supabase = typer.confirm(
        "Configure Supabase catalog support?",
        default=supabase_path.exists(),
    )
    supabase_payload = (
        _prompt_supabase_config(existing_catalog or existing_supabase_raw or {})
        if wants_supabase
        else None
    )

    wants_google = typer.confirm(
        "Enable Google Speech-to-Text meeting note transcription?",
        default=google_path.exists(),
    )
    google_payload = (
        _prompt_google_config(existing_google_raw or {})
        if wants_google
        else None
    )

    core_action = _prompt_existing_file_action(
        config_path,
        label="core app config",
        update_description="Update the guided settings and keep unrelated supported fields.",
        overwrite_description="Rewrite the file from the guided answers only.",
        skip_description="Keep the existing file unchanged.",
    )

    if wants_supabase:
        supabase_action = _prompt_existing_file_action(
            supabase_path,
            label="Supabase catalog config",
            update_description="Update the guided Supabase fields and keep unrelated keys.",
            overwrite_description="Rewrite the Supabase file from the guided answers only.",
            skip_description="Keep the existing Supabase file unchanged.",
        )
    else:
        supabase_action = _prompt_disabled_file_action(
            supabase_path,
            label="Supabase catalog config",
        )

    if wants_google:
        google_action = _prompt_existing_file_action(
            google_path,
            label="Google STT config",
            update_description="Update the guided Google fields and keep unrelated keys.",
            overwrite_description="Rewrite the Google config from the guided answers only.",
            skip_description="Keep the existing Google config unchanged.",
        )
    else:
        google_action = _prompt_disabled_file_action(
            google_path,
            label="Google STT config",
        )

    results: list[tuple[str, Path]] = []
    notes: list[str] = []

    if core_action == "skip":
        results.append(("kept existing", config_path))
    else:
        base_payload: dict[str, Any] = {}
        if core_action == "update" and existing_config_raw is not None:
            base_payload = copy.deepcopy(existing_config_raw)
        merged_core = _merge_dict(base_payload, _build_core_patch(core_answers, existing_config, core_action))
        merged_core.pop("catalog", None)
        validated_core = _minimal_core_payload(merged_core)
        _write_json(config_path, validated_core)
        results.append((_action_to_summary(core_action), config_path))

    if wants_supabase and supabase_payload is not None:
        if supabase_action == "skip":
            results.append(("kept existing", supabase_path))
        else:
            base_payload = {}
            if supabase_action == "update" and existing_supabase_raw is not None:
                base_payload = copy.deepcopy(existing_supabase_raw)
            merged_catalog = _merge_dict(base_payload, supabase_payload)
            validated_catalog = _minimal_catalog_payload(merged_catalog)
            _write_json(supabase_path, validated_catalog)
            results.append((_action_to_summary(supabase_action), supabase_path))
    elif supabase_action == "remove":
        supabase_path.unlink(missing_ok=True)
        results.append(("removed", supabase_path))
    elif supabase_action == "keep":
        results.append(("kept existing", supabase_path))
        notes.append(
            f"{supabase_path.name} was kept, so Supabase catalog support stays configured."
        )

    if wants_google and google_payload is not None:
        if google_action == "skip":
            results.append(("kept existing", google_path))
        else:
            base_payload = {}
            if google_action == "update" and existing_google_raw is not None:
                base_payload = copy.deepcopy(existing_google_raw)
            merged_google = _merge_dict(base_payload, google_payload)
            _write_json(google_path, merged_google)
            try:
                load_google_config(google_path)
            except GoogleConfigError as exc:
                raise typer.BadParameter(str(exc)) from exc
            results.append((_action_to_summary(google_action), google_path))
    elif google_action == "remove":
        google_path.unlink(missing_ok=True)
        results.append(("removed", google_path))
    elif google_action == "keep":
        results.append(("kept existing", google_path))
        notes.append(
            f"{google_path.name} was kept, so Google STT stays configured."
        )

    if not wants_supabase and core_action == "skip":
        raw_catalog = (existing_config_raw or {}).get("catalog")
        if isinstance(raw_catalog, dict) and raw_catalog:
            notes.append(
                "config.json still contains legacy catalog settings because it was kept unchanged."
            )

    final_config = load_config(config_path)
    workspace = get_workspace_path(final_config.agents.defaults.workspace)
    created_templates = sync_workspace_templates(workspace, silent=True)
    ensure_runtime_dirs()

    console.print("\n[green]Setup complete.[/green]\n")
    console.print("Files:")
    for action, path in results:
        console.print(f"  [green]•[/green] {action} [cyan]{path}[/cyan]")

    if created_templates:
        console.print("\nWorkspace templates:")
        for name in created_templates:
            console.print(f"  [green]•[/green] created [cyan]{name}[/cyan]")

    if notes:
        console.print("\nNotes:")
        for note in notes:
            console.print(f"  [yellow]•[/yellow] {note}")

    console.print("\nNext steps:")
    console.print("  1. [cyan]python -m nanobot status[/cyan]")
    console.print("  2. [cyan]python -m nanobot install-ui-command[/cyan]  [dim](optional, once per checkout)[/dim]")
    console.print("  3. [cyan]whatsapp-web-nanobot-ui[/cyan]  [dim]or[/dim]  [cyan]python -m nanobot ui[/cyan]")


def _prompt_core_config(
    existing_config: Config,
    *,
    has_existing_config: bool,
) -> dict[str, Any]:
    api_port = _prompt_port(
        "Backend API port",
        default=int(existing_config.gateway.port or 3456),
    )
    model_default = _DEFAULT_LITELLM_MODEL
    if has_existing_config and existing_config.agents.defaults.provider == "litellm":
        model_default = existing_config.agents.defaults.model or model_default
    model = _prompt_required_text(
        "LiteLLM model",
        default=model_default,
    )

    existing_base = existing_config.providers.litellm.base_url or _DEFAULT_LITELLM_BASE_URL
    base_url = _prompt_url("LiteLLM endpoint base URL", default=existing_base)
    api_key = _prompt_secret(
        "LiteLLM API key",
        existing_value=existing_config.providers.litellm.api_key,
    )

    return {
        "api_port": api_port,
        "provider": "litellm",
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
    }


def _prompt_supabase_config(existing_payload: dict[str, Any]) -> dict[str, Any]:
    tables = existing_payload.get("supabaseCatalogTables")
    if not isinstance(tables, list) or not tables:
        legacy_table = existing_payload.get("supabaseCatalogTable")
        if isinstance(legacy_table, str) and legacy_table.strip():
            tables = [legacy_table.strip()]
        else:
            tables = list(_DEFAULT_SUPABASE_TABLES)

    supabase_url = _prompt_url(
        "Supabase URL",
        default=str(existing_payload.get("supabaseUrl") or ""),
    )
    supabase_anon_key = _prompt_secret(
        "Supabase API key (service_role recommended)",
        existing_value=str(existing_payload.get("supabaseAnonKey") or ""),
    )
    project_ref = _prompt_optional_text(
        "Supabase project ref",
        default=str(existing_payload.get("supabaseProjectRef") or ""),
    )
    table_list = _prompt_csv_list(
        "Catalog tables (comma-separated)",
        default=tables,
    )
    management_token = _prompt_secret(
        "Supabase management token",
        existing_value=str(existing_payload.get("supabaseManagementToken") or ""),
        required=False,
    )

    return {
        "supabaseUrl": supabase_url,
        "supabaseAnonKey": supabase_anon_key,
        "supabaseProjectRef": project_ref,
        "supabaseCatalogTables": table_list,
        "supabaseManagementToken": management_token,
        "autoRestorePausedProject": bool(project_ref and management_token),
        "restoreTimeoutSeconds": int(existing_payload.get("restoreTimeoutSeconds") or 300),
        "cacheTtlSeconds": int(existing_payload.get("cacheTtlSeconds") or 300),
    }


def _prompt_google_config(existing_payload: dict[str, Any]) -> dict[str, Any]:
    project_id = _prompt_required_text(
        "Google project ID",
        default=str(existing_payload.get("projectId") or ""),
    )
    location = _prompt_required_text(
        "Google location",
        default=str(existing_payload.get("location") or "us"),
    )
    language_code = _prompt_required_text(
        "Google language code",
        default=str(existing_payload.get("languageCode") or "yue-Hant-HK"),
    )
    model = _prompt_required_text(
        "Google STT model",
        default=str(existing_payload.get("model") or "chirp_3"),
        validator=lambda value: "Google STT model must be exactly 'chirp_3'."
        if value != "chirp_3"
        else None,
    )
    credential_path = _prompt_google_credential_path(
        "Google credential JSON path",
        default=str(existing_payload.get("credentialJsonPath") or "secrets/google-credentials.json"),
    )

    return {
        "projectId": project_id,
        "location": location,
        "languageCode": language_code,
        "model": model,
        "credentialJsonPath": credential_path,
    }


def _build_core_patch(
    answers: dict[str, Any],
    existing_config: Config,
    action: str,
) -> dict[str, Any]:
    workspace_value = existing_config.agents.defaults.workspace
    if action in {"create", "overwrite"} or not str(workspace_value or "").strip():
        workspace_value = str(project_root())

    return {
        "gateway": {
            "port": answers["api_port"],
        },
        "agents": {
            "defaults": {
                "provider": "litellm",
                "model": answers["model"],
                "workspace": workspace_value,
            }
        },
        "channels": {
            "whatsapp": {
                "enabled": True,
                "deliveryMode": "draft",
                "bridgeUrl": "ws://localhost:3001",
            }
        },
        "providers": {
            "litellm": {
                "baseUrl": answers["base_url"],
                "apiKey": answers["api_key"],
            },
        },
    }


def _minimal_core_payload(payload: dict[str, Any]) -> dict[str, Any]:
    validated = Config.model_validate(payload)
    data = validated.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)
    _preserve_explicit_core_fields(data, validated, payload)
    return data


def _minimal_catalog_payload(payload: dict[str, Any]) -> dict[str, Any]:
    catalog = Config.model_validate({"catalog": payload}).catalog
    return catalog.model_dump(by_alias=True, exclude_defaults=True, exclude_none=True)


def _prompt_port(label: str, *, default: int) -> int:
    while True:
        value = typer.prompt(label, default=str(default)).strip()
        try:
            port = int(value)
        except ValueError:
            console.print("[red]Port must be a number.[/red]")
            continue
        if 1 <= port <= 65535:
            return port
        console.print("[red]Port must be between 1 and 65535.[/red]")


def _prompt_required_text(
    label: str,
    *,
    default: str,
    validator: Any | None = None,
) -> str:
    while True:
        value = typer.prompt(label, default=default, show_default=bool(default)).strip()
        if not value:
            console.print("[red]This value is required.[/red]")
            continue
        if validator is not None:
            error = validator(value)
            if error:
                console.print(f"[red]{error}[/red]")
                continue
        return value


def _prompt_optional_text(label: str, *, default: str = "") -> str:
    return typer.prompt(label, default=default, show_default=bool(default)).strip()


def _prompt_secret(label: str, *, existing_value: str = "", required: bool = True) -> str:
    if existing_value:
        console.print(f"[dim]{label}: press Enter to keep the current value.[/dim]")
    while True:
        value = typer.prompt(label, default="", show_default=False, hide_input=True)
        trimmed = value.strip()
        if trimmed:
            return trimmed
        if existing_value:
            return existing_value
        if not required:
            return ""
        console.print("[red]This value is required.[/red]")


def _prompt_url(label: str, *, default: str) -> str:
    def _validate(value: str) -> str | None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return "Enter a valid http:// or https:// URL."
        return None

    return _prompt_required_text(label, default=default, validator=_validate)


def _prompt_csv_list(label: str, *, default: list[str]) -> list[str]:
    default_text = ",".join(default)
    while True:
        raw = typer.prompt(label, default=default_text, show_default=True).strip()
        items = [item.strip() for item in raw.split(",") if item.strip()]
        if items:
            return items
        console.print("[red]Enter at least one value.[/red]")


def _prompt_google_credential_path(label: str, *, default: str) -> str:
    while True:
        value = typer.prompt(label, default=default, show_default=bool(default)).strip()
        if not value:
            console.print("[red]This value is required.[/red]")
            continue
        try:
            _validate_google_credential_path(value)
        except typer.BadParameter as exc:
            console.print(f"[red]{exc}[/red]")
            continue
        return value


def _validate_google_credential_path(raw_path: str) -> Path:
    candidate = resolve_project_relative(raw_path)
    try:
        candidate = confine_path(candidate)
    except ValueError as exc:
        raise typer.BadParameter(
            "Google credential file must stay inside this project checkout."
        ) from exc

    if not candidate.exists():
        raise typer.BadParameter(f"Google credential file not found: {candidate}")

    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(
            f"Google credential file is not valid JSON: {candidate}"
        ) from exc

    if not isinstance(payload, dict):
        raise typer.BadParameter(
            f"Google credential file must contain a JSON object: {candidate}"
        )

    required_keys = ("type", "client_email", "private_key", "token_uri")
    missing = [key for key in required_keys if not str(payload.get(key) or "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise typer.BadParameter(
            f"Google credential file is missing required field(s): {joined}"
        )

    return candidate


def _prompt_existing_file_action(
    path: Path,
    *,
    label: str,
    update_description: str,
    overwrite_description: str,
    skip_description: str,
) -> str:
    if not path.exists():
        return "create"

    console.print(f"\n[yellow]{label.capitalize()} already exists:[/yellow] [cyan]{path}[/cyan]")
    console.print(f"  [bold]u[/bold] = update. {update_description}")
    console.print(f"  [bold]o[/bold] = overwrite. {overwrite_description}")
    console.print(f"  [bold]s[/bold] = skip. {skip_description}")

    while True:
        choice = typer.prompt(
            f"How should Nanobot handle {path.name}?",
            default="u",
            show_default=True,
        ).strip().lower()
        mapping = {
            "u": "update",
            "update": "update",
            "o": "overwrite",
            "overwrite": "overwrite",
            "s": "skip",
            "skip": "skip",
        }
        action = mapping.get(choice)
        if action:
            return action
        console.print("[red]Enter u, o, or s.[/red]")


def _prompt_disabled_file_action(path: Path, *, label: str) -> str | None:
    if not path.exists():
        return None

    console.print(
        f"\n[yellow]{label.capitalize()} already exists but this setup run left that feature disabled:[/yellow] "
        f"[cyan]{path}[/cyan]"
    )
    console.print("  [bold]k[/bold] = keep the file unchanged")
    console.print("  [bold]r[/bold] = remove the file so the feature is no longer configured")

    while True:
        choice = typer.prompt(
            f"How should Nanobot handle {path.name}?",
            default="k",
            show_default=True,
        ).strip().lower()
        mapping = {
            "k": "keep",
            "keep": "keep",
            "r": "remove",
            "remove": "remove",
        }
        action = mapping.get(choice)
        if action:
            return action
        console.print("[red]Enter k or r.[/red]")


def _merge_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        current = base.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            base[key] = _merge_dict(copy.deepcopy(current), value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _preserve_explicit_core_fields(
    serialized: dict[str, Any],
    validated: Config,
    original: dict[str, Any],
) -> None:
    providers = original.get("providers")
    if isinstance(providers, dict) and "litellm" in providers:
        serialized.setdefault("providers", {})["litellm"] = validated.providers.litellm.model_dump(
            by_alias=True,
            exclude_none=True,
        )

    agents = original.get("agents")
    defaults = agents.get("defaults") if isinstance(agents, dict) else None
    if isinstance(defaults, dict):
        explicit_defaults: dict[str, Any] = {}
        if "provider" in defaults:
            explicit_defaults["provider"] = validated.agents.defaults.provider
        if "model" in defaults:
            explicit_defaults["model"] = validated.agents.defaults.model
        if explicit_defaults:
            serialized.setdefault("agents", {})["defaults"] = {
                **serialized.get("agents", {}).get("defaults", {}),
                **explicit_defaults,
            }

    channels = original.get("channels")
    whatsapp = channels.get("whatsapp") if isinstance(channels, dict) else None
    if isinstance(whatsapp, dict):
        explicit_whatsapp: dict[str, Any] = {}
        if "enabled" in whatsapp:
            explicit_whatsapp["enabled"] = validated.channels.whatsapp.enabled
        if "deliveryMode" in whatsapp:
            explicit_whatsapp["deliveryMode"] = validated.channels.whatsapp.delivery_mode
        if "bridgeUrl" in whatsapp:
            explicit_whatsapp["bridgeUrl"] = validated.channels.whatsapp.bridge_url
        if explicit_whatsapp:
            serialized.setdefault("channels", {})["whatsapp"] = {
                **serialized.get("channels", {}).get("whatsapp", {}),
                **explicit_whatsapp,
            }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _action_to_summary(action: str) -> str:
    return {
        "create": "created",
        "update": "updated",
        "overwrite": "overwrote",
    }[action]
