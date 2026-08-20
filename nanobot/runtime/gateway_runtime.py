"""Internal runtime helpers used by the Docker launcher.

This module intentionally contains no public CLI surface. Docker imports these
helpers directly to preserve the existing gateway behavior without exposing
local Python startup commands.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from loguru import logger

from nanobot.config.schema import Config


def make_provider(config: Config):
    """Create the configured LLM provider."""
    from nanobot.providers.azure_openai_provider import AzureOpenAIProvider
    from nanobot.providers.openai_codex_provider import OpenAICodexProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    provider_config = config.get_provider(model)

    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    if provider_name == "azure_openai":
        if not provider_config or not provider_config.api_key or not provider_config.api_base:
            raise RuntimeError("Azure OpenAI requires api_key and api_base in config.json")
        return AzureOpenAIProvider(
            api_key=provider_config.api_key,
            api_base=provider_config.api_base,
            default_model=model,
        )

    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.providers.registry import find_by_name

    spec = find_by_name(provider_name)
    if (
        not model.startswith("bedrock/")
        and not (provider_config and provider_config.api_key)
        and not (spec and spec.is_oauth)
    ):
        raise RuntimeError("No API key configured. Set one in config.json under providers.")

    return LiteLLMProvider(
        api_key=provider_config.api_key if provider_config else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=provider_config.extra_headers if provider_config else None,
        provider_name=provider_name,
    )


def maybe_enable_privacy_gateway(config: Config):
    """Route LiteLLM endpoint traffic through the local privacy gateway when enabled."""
    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    if provider_name != "litellm" or not config.privacy_gateway.enabled:
        return None

    upstream_base = config.get_api_base(model) or "http://localhost:8000/v1"
    proc = start_privacy_gateway(config, upstream_base)
    config.providers.litellm.base_url = privacy_gateway_url(config)
    return proc


def privacy_gateway_url(config: Config) -> str:
    privacy = config.privacy_gateway
    return f"http://{privacy.listen_host}:{privacy.listen_port}/v1"


def build_privacy_gateway_env(config: Config, upstream_base: str) -> dict[str, str]:
    env = {**os.environ}
    privacy = config.privacy_gateway
    env["NANOBOT_PRIVACY_UPSTREAM_BASE"] = upstream_base
    env["NANOBOT_PRIVACY_WORKSPACE"] = str(config.workspace_path)
    env["NANOBOT_PRIVACY_LISTEN_HOST"] = privacy.listen_host
    env["NANOBOT_PRIVACY_LISTEN_PORT"] = str(privacy.listen_port)
    env["NANOBOT_PRIVACY_FAIL_CLOSED"] = "true" if privacy.fail_closed else "false"
    env["NANOBOT_PRIVACY_SAVE_REDACTED_DEBUG"] = "true" if privacy.save_redacted_debug else "false"
    env["NANOBOT_PRIVACY_TEXT_ONLY_SCOPE"] = "true" if privacy.text_only_scope else "false"
    env["NANOBOT_PRIVACY_ENABLE_NER_ASSIST"] = "true" if privacy.enable_ner_assist else "false"
    return env


def privacy_gateway_running(config: Config) -> bool:
    gateway_url = privacy_gateway_url(config).removesuffix("/v1") + "/healthz"

    async def _probe() -> bool:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=0.5) as client:
                resp = await client.get(gateway_url)
                return resp.status_code == 200
        except Exception:
            return False

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_probe())

    parsed = urlparse(gateway_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8787
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_privacy_gateway(config: Config, upstream_base: str):
    """Start the local privacy gateway when LiteLLM traffic needs filtering."""
    if not config.privacy_gateway.enabled:
        return None

    if privacy_gateway_running(config):
        logger.info("Privacy gateway already running")
        return None

    env = build_privacy_gateway_env(config, upstream_base)
    logger.info("Starting privacy gateway")
    proc = subprocess.Popen(
        [sys.executable, "-m", "nanobot.privacy.gateway_server"],
        env=env,
        start_new_session=True,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Privacy gateway exited early with code {proc.returncode}")
        if privacy_gateway_running(config):
            logger.info("Privacy gateway ready")
            return proc
        time.sleep(0.2)

    stop_background_process(proc)
    raise RuntimeError("Privacy gateway did not become ready in time")


def bridge_cache_dir() -> Path:
    """Return the project-local runtime cache used by the WhatsApp bridge."""
    from nanobot.utils.paths import project_root

    return project_root() / "state" / "cache" / "whatsapp-bridge"


def prebuilt_bridge_dir() -> Path | None:
    """Return the immutable bridge bundled in the Docker image, when configured."""
    configured = str(os.environ.get("NANOBOT_PREBUILT_BRIDGE_DIR") or "").strip()
    if not configured:
        return None

    bridge_dir = Path(configured).resolve()
    entrypoint = bridge_dir / "dist" / "index.js"
    if not entrypoint.is_file():
        raise RuntimeError(
            "The prebuilt WhatsApp bridge is incomplete: "
            f"{entrypoint} does not exist. Rebuild the Docker image."
        )
    if not (bridge_dir / "node_modules").is_dir():
        raise RuntimeError(
            "The prebuilt WhatsApp bridge has no node_modules directory. "
            "Rebuild the Docker image with npm ci."
        )
    return bridge_dir


def get_bridge_dir() -> Path:
    """Return the image-bundled bridge or a deterministic development cache."""
    prebuilt = prebuilt_bridge_dir()
    if prebuilt is not None:
        return prebuilt

    user_bridge = bridge_cache_dir()

    if not shutil.which("npm"):
        raise RuntimeError("npm not found. The Docker image must include Node.js and npm.")

    pkg_bridge = Path(__file__).parent.parent / "bridge"
    src_bridge = Path(__file__).parent.parent.parent / "bridge"

    source: Path | None = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if source is None:
        raise RuntimeError("Bridge source not found in the Docker image or project checkout.")

    if (user_bridge / "dist" / "index.js").exists() and not bridge_needs_refresh(source, user_bridge):
        return user_bridge

    logger.info("Setting up WhatsApp bridge")
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    try:
        subprocess.run(["npm", "ci"], cwd=user_bridge, check=True, capture_output=True)
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise RuntimeError(f"WhatsApp bridge build failed: {stderr[:500]}") from exc

    return user_bridge


def bridge_start_command(bridge_dir: Path) -> list[str]:
    """Return the direct Node command for an already-built bridge."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found. The Docker image must include Node.js.")
    entrypoint = bridge_dir / "dist" / "index.js"
    if not entrypoint.is_file():
        raise RuntimeError(f"WhatsApp bridge entrypoint not found: {entrypoint}")
    return [node, str(entrypoint)]


def bridge_needs_refresh(source: Path, cached: Path) -> bool:
    cached_entry = cached / "dist" / "index.js"
    if not cached_entry.exists():
        return True

    def latest_mtime(root: Path) -> float:
        latest = 0.0
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if rel.parts and rel.parts[0] in {"node_modules", "dist"}:
                continue
            latest = max(latest, path.stat().st_mtime)
        return latest

    return latest_mtime(source) > latest_mtime(cached)


def build_whatsapp_bridge_env(config: Config) -> dict[str, str]:
    """Build environment variables for the WhatsApp bridge process."""
    env = {**os.environ}
    wa = config.channels.whatsapp
    from nanobot.utils.paths import project_root

    root = project_root()

    def set_runtime_value(name: str, config_value: str) -> None:
        runtime_value = os.environ.get(name)
        if runtime_value:
            env[name] = runtime_value
        elif config_value:
            env[name] = config_value

    set_runtime_value("BRIDGE_TOKEN", wa.bridge_token)
    set_runtime_value("WEB_BROWSER_MODE", wa.web_browser_mode)
    set_runtime_value("WEB_CDP_URL", wa.web_cdp_url)
    set_runtime_value("WEB_CDP_CHROME_PATH", wa.web_cdp_chrome_path)
    set_runtime_value("WEB_PROFILE_DIR", wa.web_profile_dir)
    set_runtime_value("WEB_HOST_PROFILE_DIR", str(root / "whatsapp-web"))
    set_runtime_value("AUTH_DIR", str(root / "whatsapp-auth"))

    helper_url = str(os.environ.get("WEB_CDP_HELPER_URL") or "").strip()
    if helper_url:
        env["WEB_CDP_HELPER_URL"] = helper_url

    helper_token = str(os.environ.get("WEB_CDP_HELPER_TOKEN") or "").strip()
    if helper_token:
        env["WEB_CDP_HELPER_TOKEN"] = helper_token

    helper_platform = str(os.environ.get("WEB_CDP_HELPER_PLATFORM") or "").strip()
    if helper_platform:
        env["WEB_CDP_HELPER_PLATFORM"] = helper_platform

    parsed = urlparse(wa.bridge_url)
    if parsed.port:
        set_runtime_value("BRIDGE_PORT", str(parsed.port))
    return env


def ensure_whatsapp_bridge_browser(bridge_dir: Path, config: Config, env: dict[str, str]) -> None:
    """Install Playwright Chromium when draft mode uses browser launch mode."""
    if config.channels.whatsapp.delivery_mode != "draft":
        return
    if config.channels.whatsapp.web_browser_mode != "launch":
        logger.info("Using CDP browser mode for WhatsApp Web; skipping Playwright Chromium install")
        return

    if os.environ.get("NANOBOT_PREBUILT_BRIDGE_DIR", "").strip():
        raise RuntimeError(
            "The release container cannot download Playwright Chromium at startup. "
            "Set channels.whatsapp.web_browser_mode to 'cdp' and install the host "
            "CDP helper, or build a custom image that already contains Chromium."
        )

    try:
        subprocess.run(
            ["npx", "playwright", "install", "chromium"],
            cwd=bridge_dir,
            check=True,
            env=env,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else ""
        raise RuntimeError(f"Playwright browser install failed: {stderr[:500]}") from exc


def whatsapp_bridge_running(config: Config) -> bool:
    """Return True when the WhatsApp bridge accepts a WebSocket connection."""
    bridge_url = config.channels.whatsapp.bridge_url

    async def _probe() -> bool:
        import websockets

        try:
            async with websockets.connect(
                bridge_url,
                open_timeout=0.5,
                close_timeout=0.5,
                ping_interval=None,
            ):
                await asyncio.sleep(0.2)
                return True
        except Exception:
            return False

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_probe())

    parsed = urlparse(bridge_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 3001
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def start_whatsapp_bridge(config: Config):
    """Start the WhatsApp bridge in the background when enabled."""
    if not config.channels.whatsapp.enabled:
        return None

    if whatsapp_bridge_running(config):
        logger.info("WhatsApp bridge already running")
        return None

    bridge_dir = get_bridge_dir()
    env = build_whatsapp_bridge_env(config)
    ensure_whatsapp_bridge_browser(bridge_dir, config, env)

    logger.info("Starting WhatsApp bridge")
    proc = subprocess.Popen(
        bridge_start_command(bridge_dir),
        cwd=bridge_dir,
        env=env,
        start_new_session=True,
    )

    # Importing Baileys and its transitive modules can take more than ten
    # seconds on Docker Desktop, especially on the first Windows launch.
    # Keep the timeout bounded while allowing operators to tune slow hosts.
    try:
        startup_timeout = float(os.environ.get("BRIDGE_STARTUP_TIMEOUT_SECONDS", "60"))
    except ValueError:
        startup_timeout = 60.0
    startup_timeout = min(max(startup_timeout, 5.0), 120.0)
    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"WhatsApp bridge exited early with code {proc.returncode}")
        if whatsapp_bridge_running(config):
            logger.info("WhatsApp bridge ready")
            return proc
        time.sleep(0.2)

    stop_whatsapp_bridge(proc)
    raise RuntimeError(
        f"WhatsApp bridge did not become ready within {startup_timeout:g} seconds"
    )


def stop_whatsapp_bridge(proc) -> None:
    stop_background_process(proc)


def stop_background_process(proc) -> None:
    """Stop a subprocess started by the Docker runtime."""
    if proc is None or proc.poll() is not None:
        return

    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGTERM)
        else:
            proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass
