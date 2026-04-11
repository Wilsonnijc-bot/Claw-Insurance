"""CLI commands for nanobot."""

import asyncio
import os
import select
import shlex
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

# Force UTF-8 encoding for Windows console
if sys.platform == "win32":
    import locale
    if sys.stdout.encoding != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
        # Re-open stdout/stderr with UTF-8 encoding
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from nanobot import __logo__, __version__
from nanobot.config.schema import Config
from nanobot.utils.helpers import sync_workspace_templates

app = typer.Typer(
    name="nanobot",
    help=f"{__logo__} nanobot - Personal AI Assistant",
    no_args_is_help=True,
)

console = Console()
EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit", ":q"}
DEV_RUNTIME_PORTS = (5173, 5174, 3456, 3001)
_DEV_RUNTIME_ROOT_TOKENS = (
    "-m nanobot ui",
    "-m nanobot launcher",
    "-m nanobot.privacy.gateway_server",
)
_DEV_RUNTIME_PARENT_TOKENS = ("npm run dev", "npm start")
_DEV_RUNTIME_CHILD_TOKENS = (
    "node_modules/.bin/vite",
    "node dist/index.js",
    "esbuild --service=",
)

# ---------------------------------------------------------------------------
# CLI input: prompt_toolkit for editing, paste, history, and display
# ---------------------------------------------------------------------------

_PROMPT_SESSION: PromptSession | None = None
_SAVED_TERM_ATTRS = None  # original termios settings, restored on exit


def _run_text_command(args: list[str]) -> str:
    """Run a local command and return stdout as text."""
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return ""
    return result.stdout or ""


def _read_process_table() -> dict[int, tuple[int, str]]:
    """Return ``pid -> (ppid, command)`` for local processes."""
    processes: dict[int, tuple[int, str]] = {}
    output = _run_text_command(["ps", "-axo", "pid=,ppid=,command="])
    for line in output.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        processes[pid] = (ppid, parts[2])
    return processes


def _list_listening_pids(ports: tuple[int, ...]) -> set[int]:
    """Return PIDs listening on the requested TCP ports."""
    if not ports:
        return set()
    args = ["lsof", "-nP", "-t"]
    args.extend(f"-iTCP:{port}" for port in ports)
    args.append("-sTCP:LISTEN")
    output = _run_text_command(args)
    return {int(line) for line in output.splitlines() if line.strip().isdigit()}


def _command_contains_any(command: str, tokens: tuple[str, ...]) -> bool:
    return any(token in command for token in tokens)


def _is_dev_runtime_root_command(command: str) -> bool:
    return _command_contains_any(command, _DEV_RUNTIME_ROOT_TOKENS)


def _is_dev_runtime_parent_command(command: str) -> bool:
    return _command_contains_any(command, _DEV_RUNTIME_PARENT_TOKENS)


def _is_dev_runtime_child_command(command: str) -> bool:
    return (
        _is_dev_runtime_root_command(command)
        or _is_dev_runtime_parent_command(command)
        or _command_contains_any(command, _DEV_RUNTIME_CHILD_TOKENS)
    )


def _collect_nanobot_dev_runtime_pids() -> dict[int, str]:
    """Collect local Nanobot UI runtime PIDs from ports plus process signatures."""
    processes = _read_process_table()
    if not processes:
        return {}

    children: dict[int, set[int]] = defaultdict(set)
    for pid, (ppid, _command) in processes.items():
        children[ppid].add(pid)

    targets: dict[int, str] = {}
    for pid, (_ppid, command) in processes.items():
        if _is_dev_runtime_root_command(command):
            targets[pid] = command

    for pid in _list_listening_pids(DEV_RUNTIME_PORTS):
        if pid in processes:
            targets[pid] = processes[pid][1]

    queue = list(targets)
    index = 0
    while index < len(queue):
        pid = queue[index]
        index += 1

        current = pid
        while current in processes:
            parent_pid = processes[current][0]
            if parent_pid not in processes:
                break
            parent_command = processes[parent_pid][1]
            if not (_is_dev_runtime_root_command(parent_command) or _is_dev_runtime_parent_command(parent_command)):
                break
            if parent_pid not in targets:
                targets[parent_pid] = parent_command
                queue.append(parent_pid)
            current = parent_pid

        for child_pid in children.get(pid, set()):
            child_command = processes[child_pid][1]
            if _is_dev_runtime_child_command(child_command) and child_pid not in targets:
                targets[child_pid] = child_command
                queue.append(child_pid)

    return dict(sorted(targets.items()))


def _signal_pids(pids: set[int] | list[int], sig: int) -> set[int]:
    """Send one signal to every PID that still exists."""
    signaled: set[int] = set()
    for pid in sorted(set(pids)):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            continue
        except PermissionError:
            continue
        signaled.add(pid)
    return signaled


def _live_pids(pids: set[int] | list[int]) -> set[int]:
    """Return the subset of PIDs that are still alive."""
    live: set[int] = set()
    for pid in set(pids):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            live.add(pid)
        else:
            live.add(pid)
    return live


def _stop_local_dev_runtime(wait_seconds: float = 1.0) -> tuple[dict[int, str], set[int], set[int], set[int]]:
    """Stop the local Nanobot UI development runtime."""
    matched = _collect_nanobot_dev_runtime_pids()
    if not matched:
        return {}, set(), set(), set()

    terminated = _signal_pids(set(matched), signal.SIGTERM)
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    remaining = _live_pids(set(matched))
    killed: set[int] = set()
    if remaining:
        killed = _signal_pids(remaining, signal.SIGKILL)
        time.sleep(0.2)
        remaining = _live_pids(remaining)

    return matched, terminated, killed, remaining


def _flush_pending_tty_input() -> None:
    """Drop unread keypresses typed while the model was generating output."""
    try:
        fd = sys.stdin.fileno()
        if not os.isatty(fd):
            return
    except Exception:
        return

    try:
        import termios
        termios.tcflush(fd, termios.TCIFLUSH)
        return
    except Exception:
        pass

    try:
        while True:
            ready, _, _ = select.select([fd], [], [], 0)
            if not ready:
                break
            if not os.read(fd, 4096):
                break
    except Exception:
        return


def _restore_terminal() -> None:
    """Restore terminal to its original state (echo, line buffering, etc.)."""
    if _SAVED_TERM_ATTRS is None:
        return
    try:
        import termios
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, _SAVED_TERM_ATTRS)
    except Exception:
        pass


def _init_prompt_session() -> None:
    """Create the prompt_toolkit session with persistent file history."""
    global _PROMPT_SESSION, _SAVED_TERM_ATTRS

    # Save terminal state so we can restore it on exit
    try:
        import termios
        _SAVED_TERM_ATTRS = termios.tcgetattr(sys.stdin.fileno())
    except Exception:
        pass

    history_file = Path(__file__).resolve().parents[2] / ".cli-history"
    history_file.parent.mkdir(parents=True, exist_ok=True)

    _PROMPT_SESSION = PromptSession(
        history=FileHistory(str(history_file)),
        enable_open_in_editor=False,
        multiline=False,   # Enter submits (single line mode)
    )


def _print_agent_response(response: str, render_markdown: bool) -> None:
    """Render assistant response with consistent terminal styling."""
    content = response or ""
    body = Markdown(content) if render_markdown else Text(content)
    console.print()
    console.print(f"[cyan]{__logo__} nanobot[/cyan]")
    console.print(body)
    console.print()


def _is_exit_command(command: str) -> bool:
    """Return True when input should end interactive chat."""
    return command.lower() in EXIT_COMMANDS


async def _read_interactive_input_async() -> str:
    """Read user input using prompt_toolkit (handles paste, history, display).

    prompt_toolkit natively handles:
    - Multiline paste (bracketed paste mode)
    - History navigation (up/down arrows)
    - Clean display (no ghost characters or artifacts)
    """
    if _PROMPT_SESSION is None:
        raise RuntimeError("Call _init_prompt_session() first")
    try:
        with patch_stdout():
            return await _PROMPT_SESSION.prompt_async(
                HTML("<b fg='ansiblue'>You:</b> "),
            )
    except EOFError as exc:
        raise KeyboardInterrupt from exc



def version_callback(value: bool):
    if value:
        console.print(f"{__logo__} nanobot v{__version__}")
        raise typer.Exit()


def whatsapp_web_nanobot_gateway_entry() -> None:
    """Console-script alias for `nanobot gateway`."""
    sys.argv = ["whatsapp-web-nanobot-gateway", "gateway", *sys.argv[1:]]
    app()


def whatsapp_web_nanobot_ui_entry() -> None:
    """Console-script alias for `nanobot ui`."""
    sys.argv = ["whatsapp-web-nanobot-ui", "ui", *sys.argv[1:]]
    app()


@app.callback()
def main(
    version: bool = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True
    ),
):
    """nanobot - Personal AI Assistant."""
    pass


def _frontend_path_candidates() -> list[Path]:
    """Return project-local locations of the React frontend."""
    project_root = Path(__file__).resolve().parents[2]
    candidates: list[Path] = []

    for env_name in ("NANOBOT_UI_PATH", "NANOBOT_FRONTEND_PATH"):
        value = os.environ.get(env_name, "").strip()
        if value:
            candidates.append(Path(value).expanduser())

    candidates.extend([
        project_root / "Insurance frontend",
    ])

    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _looks_like_frontend_dir(path: Path) -> bool:
    """Return True when the path looks like the Vite frontend project."""
    return (
        path.exists()
        and path.is_dir()
        and (path / "package.json").exists()
        and (path / "vite.config.ts").exists()
        and (path / "src").exists()
    )


def _resolve_frontend_dir(frontend_path: str = "") -> Path:
    """Resolve the React frontend directory for `nanobot ui`."""
    if frontend_path.strip():
        candidate = Path(frontend_path).expanduser()
        if _looks_like_frontend_dir(candidate):
            return candidate
        console.print(f"[red]Frontend path is not a valid Vite app: {candidate}[/red]")
        raise typer.Exit(1)

    for candidate in _frontend_path_candidates():
        if _looks_like_frontend_dir(candidate):
            return candidate

    console.print(
        "[red]Could not find the frontend project. Set NANOBOT_UI_PATH or pass --path to `nanobot ui`.[/red]"
    )
    raise typer.Exit(1)


def _preferred_global_bin_dir() -> Path:
    """Choose a stable bin dir for wrapper commands on the current machine."""
    override = os.environ.get("NANOBOT_BIN_DIR", "").strip()
    if override:
        return Path(override).expanduser()

    home = Path.home()
    candidates = [
        home / ".local" / "bin",
        home / "bin",
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    ]
    for candidate in candidates:
        parent = candidate if candidate.exists() else candidate.parent
        try:
            if parent.exists() and os.access(parent, os.W_OK):
                return candidate
        except Exception:
            continue
    return home / ".local" / "bin"


def _shell_rc_candidates() -> list[Path]:
    home = Path.home()
    shell = Path(os.environ.get("SHELL", "")).name
    if shell == "bash":
        return [home / ".bashrc", home / ".bash_profile"]
    if shell == "zsh":
        return [home / ".zshrc"]
    return [home / ".zshrc", home / ".bashrc", home / ".profile"]


def _ensure_dir_in_shell_path(bin_dir: Path) -> Path | None:
    """Add the install dir to the user's shell startup file when needed."""
    resolved = bin_dir.expanduser().resolve()
    path_entries = [Path(p).expanduser().resolve() for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if resolved in path_entries:
        return None

    export_line = f'export PATH="{resolved}:$PATH"'
    for rc_path in _shell_rc_candidates():
        try:
            existing = rc_path.read_text(encoding="utf-8") if rc_path.exists() else ""
            if export_line in existing:
                return rc_path
            rc_path.parent.mkdir(parents=True, exist_ok=True)
            prefix = "\n" if existing and not existing.endswith("\n") else ""
            rc_path.write_text(existing + prefix + export_line + "\n", encoding="utf-8")
            return rc_path
        except Exception:
            continue
    return None


def _write_wrapper_script(target: Path, *, command_name: str, subcommand: str) -> None:
    """Write a stable wrapper command bound to this project checkout."""
    project_root = Path(__file__).resolve().parents[2]
    venv_python = project_root / ".venv" / "bin" / "python"
    config_path = project_root / "config.json"
    script = f"""#!/bin/sh
set -eu
PROJECT_ROOT={shlex.quote(str(project_root))}
CONFIG_PATH={shlex.quote(str(config_path))}
VENV_PYTHON={shlex.quote(str(venv_python))}
if [ -x \"$VENV_PYTHON\" ]; then
  PYTHON_BIN=\"$VENV_PYTHON\"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN=\"$(command -v python3)\"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN=\"$(command -v python)\"
else
  echo \"Python is required for {command_name}\" >&2
  exit 1
fi
export NANOBOT_CONFIG_PATH=\"$CONFIG_PATH\"
if [ -n \"${{PYTHONPATH:-}}\" ]; then
  export PYTHONPATH=\"$PROJECT_ROOT:$PYTHONPATH\"
else
  export PYTHONPATH=\"$PROJECT_ROOT\"
fi
cd \"$PROJECT_ROOT\"
exec \"$PYTHON_BIN\" -m nanobot {subcommand} \"$@\"
"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(script, encoding="utf-8")
    target.chmod(0o755)


def _api_launcher_running(api_port: int) -> bool:
    """Return True when a Nanobot API/launcher server is reachable on the port."""
    import json
    import urllib.request

    url = f"http://127.0.0.1:{api_port}/api/status"
    try:
        with urllib.request.urlopen(url, timeout=0.6) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8", errors="ignore") or "{}")
            return isinstance(payload, dict) and (
                "status" in payload or "gateway_ready" in payload or "gateway_starting" in payload
            )
    except Exception:
        return False


def _ensure_api_launcher_running(api_port: int, config_path: str | None = None) -> None:
    """Start the lightweight launcher when the frontend API endpoint is not up yet."""
    import subprocess

    if _api_launcher_running(api_port):
        return

    project_root = Path(__file__).resolve().parents[2]
    command = [sys.executable, "-m", "nanobot", "launcher", "--api-port", str(api_port)]
    if config_path:
        command.extend(["--config", config_path])

    subprocess.Popen(
        command,
        cwd=project_root,
        env={**os.environ},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        if _api_launcher_running(api_port):
            return
        time.sleep(0.2)

    console.print(
        f"[red]Could not reach the Nanobot launcher on port {api_port}. "
        "Start `nanobot launcher` or `nanobot gateway` manually.[/red]"
    )
    raise typer.Exit(1)


@app.command()
def ui(
    path: str = typer.Option(
        "",
        "--path",
        help="Path to the React/Vite frontend project. Defaults to NANOBOT_UI_PATH or nearby 'Insurance frontend'.",
    ),
    api_port: int = typer.Option(3456, "--api-port", help="Launcher/API port used by the frontend proxy."),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path for the launcher."),
):
    """Start the React UI and ensure the lightweight API launcher is available first."""
    import subprocess

    frontend_dir = _resolve_frontend_dir(path)
    _ensure_api_launcher_running(api_port, config)
    console.print(f"{__logo__} Starting UI from [cyan]{frontend_dir}[/cyan]")
    console.print(
        "UI is using the launcher on "
        f"[cyan]localhost:{api_port}[/cyan]. Login in the frontend will call /api/login "
        "and boot the global gateway on demand.\n"
    )

    try:
        subprocess.run(["npm", "run", "dev"], cwd=frontend_dir, check=True, env={**os.environ})
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Frontend failed: {e}[/red]")
        raise typer.Exit(e.returncode or 1)
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")
        raise typer.Exit(1)


@app.command("stop-dev")
def stop_dev():
    """Stop the local Nanobot UI development runtime."""
    matched, terminated, killed, remaining = _stop_local_dev_runtime()

    if not matched:
        console.print(
            "[green]✓[/green] No active Nanobot local dev processes found "
            "(checked ports 5173, 5174, 3456, 3001)"
        )
        return

    stopped = terminated | killed
    console.print(
        f"[green]✓[/green] Stopped {len(stopped)} Nanobot local dev process(es) "
        "(checked ports 5173, 5174, 3456, 3001)"
    )
    if remaining:
        console.print(
            "[red]Some Nanobot local dev processes are still running: "
            f"{', '.join(str(pid) for pid in sorted(remaining))}[/red]"
        )
        raise typer.Exit(1)


@app.command("install-ui-command")
def install_ui_command(
    bin_dir: str = typer.Option(
        "",
        "--bin-dir",
        help="Install directory for the global wrapper commands. Defaults to ~/.local/bin, ~/bin, or a writable system bin.",
    ),
    update_shell: bool = typer.Option(
        True,
        "--update-shell/--no-update-shell",
        help="Add the install directory to your shell PATH when needed.",
    ),
):
    """Install stable global wrapper commands for this project checkout."""
    install_dir = Path(bin_dir).expanduser() if bin_dir.strip() else _preferred_global_bin_dir()
    install_dir.mkdir(parents=True, exist_ok=True)

    ui_path = install_dir / "whatsapp-web-nanobot-ui"
    gateway_path = install_dir / "whatsapp-web-nanobot-gateway"
    _write_wrapper_script(ui_path, command_name="whatsapp-web-nanobot-ui", subcommand="ui")
    _write_wrapper_script(gateway_path, command_name="whatsapp-web-nanobot-gateway", subcommand="gateway")

    rc_path = _ensure_dir_in_shell_path(install_dir) if update_shell else None

    console.print(f"[green]✓[/green] Installed [cyan]{ui_path}[/cyan]")
    console.print(f"[green]✓[/green] Installed [cyan]{gateway_path}[/cyan]")
    if rc_path is not None:
        console.print(f"[green]✓[/green] Added [cyan]{install_dir}[/cyan] to PATH in [cyan]{rc_path}[/cyan]")
        console.print("[yellow]Open a new terminal window or run `source` on that shell file before using the commands.[/yellow]")
    elif update_shell:
        console.print(f"[green]✓[/green] PATH already includes [cyan]{install_dir}[/cyan] or no update was needed")

    console.print("\nGlobal commands for this project checkout:")
    console.print("  [cyan]whatsapp-web-nanobot-ui[/cyan]")
    console.print("  [cyan]whatsapp-web-nanobot-gateway[/cyan]")


# ============================================================================
# Onboard / Setup
# ============================================================================


@app.command("setup")
def setup():
    """Run guided setup and write split config files."""
    from nanobot.cli.setup_wizard import run_setup_wizard

    run_setup_wizard()


@app.command()
def onboard():
    """Initialize nanobot configuration and workspace."""
    from nanobot.config.loader import get_config_path, load_config, save_config
    from nanobot.config.schema import Config
    from nanobot.utils.helpers import get_workspace_path

    config_path = get_config_path()

    if config_path.exists():
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("  [bold]y[/bold] = overwrite with defaults (existing values will be lost)")
        console.print("  [bold]N[/bold] = refresh config, keeping existing values and adding new fields")
        if typer.confirm("Overwrite?"):
            config = Config()
            save_config(config)
            console.print(f"[green]✓[/green] Config reset to defaults at {config_path}")
        else:
            config = load_config()
            save_config(config)
            console.print(f"[green]✓[/green] Config refreshed at {config_path} (existing values preserved)")
    else:
        save_config(Config())
        console.print(f"[green]✓[/green] Created config at {config_path}")

    # Create workspace
    workspace = get_workspace_path()

    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)
        console.print(f"[green]✓[/green] Created workspace at {workspace}")

    sync_workspace_templates(workspace)

    console.print(f"\n{__logo__} nanobot is ready!")
    console.print("\nNext steps:")
    console.print("  1. Add your API key to [cyan]config.json[/cyan]")
    console.print("     Get one at: https://openrouter.ai/keys")
    console.print("  2. Chat: [cyan]nanobot agent -m \"Hello!\"[/cyan]")
    console.print("\n[dim]Want Telegram/WhatsApp? See: https://github.com/HKUDS/nanobot#-chat-apps[/dim]")





def _make_provider(config: Config):
    """Create the appropriate LLM provider from config."""
    from nanobot.providers.openai_codex_provider import OpenAICodexProvider
    from nanobot.providers.azure_openai_provider import AzureOpenAIProvider

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)

    # OpenAI Codex (OAuth)
    if provider_name == "openai_codex" or model.startswith("openai-codex/"):
        return OpenAICodexProvider(default_model=model)

    # Custom: direct OpenAI-compatible endpoint, bypasses LiteLLM
    from nanobot.providers.custom_provider import CustomProvider
    if provider_name == "custom":
        return CustomProvider(
            api_key=p.api_key if p else "no-key",
            api_base=config.get_api_base(model) or "http://localhost:8000/v1",
            default_model=model,
        )

    # Azure OpenAI: direct Azure OpenAI endpoint with deployment name
    if provider_name == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            console.print("[red]Error: Azure OpenAI requires api_key and api_base.[/red]")
            console.print("Set them in config.json under providers.azure_openai section")
            console.print("Use the model field to specify the deployment name.")
            raise typer.Exit(1)
        
        return AzureOpenAIProvider(
            api_key=p.api_key,
            api_base=p.api_base,
            default_model=model,
        )

    from nanobot.providers.litellm_provider import LiteLLMProvider
    from nanobot.providers.registry import find_by_name
    spec = find_by_name(provider_name)
    if not model.startswith("bedrock/") and not (p and p.api_key) and not (spec and spec.is_oauth):
        console.print("[red]Error: No API key configured.[/red]")
        console.print("Set one in config.json under providers section")
        raise typer.Exit(1)

    return LiteLLMProvider(
        api_key=p.api_key if p else None,
        api_base=config.get_api_base(model),
        default_model=model,
        extra_headers=p.extra_headers if p else None,
        provider_name=provider_name,
    )


def _maybe_enable_privacy_gateway(config: Config):
    """Route custom-provider traffic through the local privacy gateway when enabled."""
    # Privacy pipeline step 2:
    # - decide whether the privacy gateway should sit in front of the real cloud endpoint
    # - keep the real upstream URL for the child gateway process
    # - rewrite the active custom provider to point at the local gateway instead
    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    if provider_name != "custom" or not config.privacy_gateway.enabled:
        return None

    upstream_base = config.get_api_base(model) or "http://localhost:8000/v1"
    proc = _start_privacy_gateway(config, upstream_base)
    config.providers.custom.api_base = _privacy_gateway_url(config)
    return proc


# ============================================================================
# Gateway / Server
# ============================================================================


@app.command()
def gateway(
    port: int = typer.Option(3456, "--port", "-p", help="Legacy gateway port placeholder (API uses --api-port)."),
    api_port: int = typer.Option(3456, "--api-port", help="API server port for the frontend UI"),
    workspace: str | None = typer.Option(None, "--workspace", "-w", help="Workspace directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the nanobot gateway."""
    from nanobot.agent.loop import AgentLoop
    from nanobot.api.server import ApiServer
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.loader import load_config
    from nanobot.cron.service import CronService
    from nanobot.cron.types import CronJob
    from nanobot.heartbeat.service import HeartbeatService
    from nanobot.session.manager import SessionManager

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    config_path = Path(config) if config else None
    config = load_config(config_path)
    if workspace:
        config.agents.defaults.workspace = workspace
    bridge_proc = _start_whatsapp_bridge(config)
    privacy_proc = _maybe_enable_privacy_gateway(config)

    console.print(f"{__logo__} Starting nanobot gateway (API on port {api_port})...")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()
    provider = _make_provider(config)
    session_manager = SessionManager(config.workspace_path)

    # Create cron service first (callback set after agent creation)
    # Use workspace path for per-instance cron store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # Create agent with cron service
    agent = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        session_manager=session_manager,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        privacy_config=config.privacy_gateway,
    )

    # Set cron callback (needs agent)
    async def on_cron_job(job: CronJob) -> str | None:
        """Execute a cron job through the agent."""
        from nanobot.agent.tools.cron import CronTool
        from nanobot.agent.tools.message import MessageTool
        reminder_note = (
            "[Scheduled Task] Timer finished.\n\n"
            f"Task '{job.name}' has been triggered.\n"
            f"Scheduled instruction: {job.payload.message}"
        )

        # Prevent the agent from scheduling new cron jobs during execution
        cron_tool = agent.tools.get("cron")
        cron_token = None
        if isinstance(cron_tool, CronTool):
            cron_token = cron_tool.set_cron_context(True)
        try:
            response = await agent.process_direct(
                reminder_note,
                session_key=f"cron:{job.id}",
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to or "direct",
            )
        finally:
            if isinstance(cron_tool, CronTool) and cron_token is not None:
                cron_tool.reset_cron_context(cron_token)

        message_tool = agent.tools.get("message")
        if isinstance(message_tool, MessageTool) and message_tool._sent_in_turn:
            return response

        if job.payload.deliver and job.payload.to and response:
            from nanobot.bus.events import OutboundMessage
            await bus.publish_outbound(OutboundMessage(
                channel=job.payload.channel or "cli",
                chat_id=job.payload.to,
                content=response
            ))
        return response
    cron.on_job = on_cron_job

    # Create channel manager
    channels = ChannelManager(config, bus)

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        enabled = set(channels.enabled_channels)
        # Prefer the most recently updated non-internal session on an enabled channel.
        for item in session_manager.list_sessions():
            key = item.get("key") or ""
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system"}:
                continue
            if channel in enabled and chat_id:
                return channel, chat_id
        # Fallback keeps prior behavior but remains explicit.
        return "cli", "direct"

    # Create heartbeat service
    async def on_heartbeat_execute(tasks: str) -> str:
        """Phase 2: execute heartbeat tasks through the full agent loop."""
        channel, chat_id = _pick_heartbeat_target()

        async def _silent(*_args, **_kwargs):
            pass

        return await agent.process_direct(
            tasks,
            session_key="heartbeat",
            channel=channel,
            chat_id=chat_id,
            on_progress=_silent,
        )

    async def on_heartbeat_notify(response: str) -> None:
        """Deliver a heartbeat response to the user's channel."""
        from nanobot.bus.events import OutboundMessage
        channel, chat_id = _pick_heartbeat_target()
        if channel == "cli":
            return  # No external channel available to deliver to
        await bus.publish_outbound(OutboundMessage(channel=channel, chat_id=chat_id, content=response))

    hb_cfg = config.gateway.heartbeat
    heartbeat = HeartbeatService(
        workspace=config.workspace_path,
        provider=provider,
        model=agent.model,
        on_execute=on_heartbeat_execute,
        on_notify=on_heartbeat_notify,
        interval_s=hb_cfg.interval_s,
        enabled=hb_cfg.enabled,
    )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    cron_status = cron.status()
    if cron_status["jobs"] > 0:
        console.print(f"[green]✓[/green] Cron: {cron_status['jobs']} scheduled jobs")

    console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")

    # Create API server for frontend UI
    api_server = ApiServer(
        config=config,
        bus=bus,
        session_manager=session_manager,
        agent=agent,
        channel_manager=channels,
    )
    console.print(f"[green]✓[/green] API server will start on port {api_port}")

    async def run():
        try:
            await cron.start()
            await heartbeat.start()
            await api_server.start(port=api_port)
            await asyncio.gather(
                agent.run(),
                channels.start_all(),
            )
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        finally:
            await api_server.stop()
            await agent.close_mcp()
            heartbeat.stop()
            cron.stop()
            agent.stop()
            await channels.stop_all()
            _stop_whatsapp_bridge(bridge_proc)
            _stop_background_process(privacy_proc)

    asyncio.run(run())


@app.command()
def launcher(
    api_port: int = typer.Option(3456, "--api-port", help="API server port for the frontend UI"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start the lightweight launcher so the UI can load before the full gateway starts."""
    from nanobot.api.launcher import LauncherServer
    from nanobot.config.loader import load_config

    if verbose:
        import logging
        logging.basicConfig(level=logging.DEBUG)

    config_path = Path(config) if config else None
    loaded_config = load_config(config_path)

    async def run() -> None:
        server = LauncherServer(config=loaded_config, api_port=api_port)
        try:
            await server.start()
            await asyncio.Event().wait()
        finally:
            await server.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        console.print("\nShutting down launcher...")


# ============================================================================
# Agent Commands
# ============================================================================


@app.command()
def agent(
    message: str = typer.Option(None, "--message", "-m", help="Message to send to the agent"),
    session_id: str = typer.Option("cli:direct", "--session", "-s", help="Session ID"),
    markdown: bool = typer.Option(True, "--markdown/--no-markdown", help="Render assistant output as Markdown"),
    logs: bool = typer.Option(False, "--logs/--no-logs", help="Show nanobot runtime logs during chat"),
):
    """Interact with the agent directly."""
    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import get_data_dir, load_config
    from nanobot.cron.service import CronService

    config = load_config()
    sync_workspace_templates(config.workspace_path)
    privacy_proc = _maybe_enable_privacy_gateway(config)

    bus = MessageBus()
    provider = _make_provider(config)

    # Create cron service for tool usage (no callback needed for CLI unless running)
    cron_store_path = get_data_dir() / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    if logs:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.defaults.model,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=config.agents.defaults.memory_window,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        brave_api_key=config.tools.web.search.api_key or None,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        privacy_config=config.privacy_gateway,
    )

    # Show spinner when logs are off (no output to miss); skip when logs are on
    def _thinking_ctx():
        if logs:
            from contextlib import nullcontext
            return nullcontext()
        # Animated spinner is safe to use with prompt_toolkit input handling
        return console.status("[dim]nanobot is thinking...[/dim]", spinner="dots")

    async def _cli_progress(content: str, *, tool_hint: bool = False) -> None:
        ch = agent_loop.channels_config
        if ch and tool_hint and not ch.send_tool_hints:
            return
        if ch and not tool_hint and not ch.send_progress:
            return
        console.print(f"  [dim]↳ {content}[/dim]")

    if message:
        # Single message mode — direct call, no bus needed
        async def run_once():
            try:
                with _thinking_ctx():
                    response = await agent_loop.process_direct(message, session_id, on_progress=_cli_progress)
                _print_agent_response(response, render_markdown=markdown)
            finally:
                await agent_loop.close_mcp()
                _stop_background_process(privacy_proc)

        asyncio.run(run_once())
    else:
        # Interactive mode — route through bus like other channels
        from nanobot.bus.events import InboundMessage
        _init_prompt_session()
        console.print(f"{__logo__} Interactive mode (type [bold]exit[/bold] or [bold]Ctrl+C[/bold] to quit)\n")

        if ":" in session_id:
            cli_channel, cli_chat_id = session_id.split(":", 1)
        else:
            cli_channel, cli_chat_id = "cli", session_id

        def _handle_signal(signum, frame):
            sig_name = signal.Signals(signum).name
            _restore_terminal()
            console.print(f"\nReceived {sig_name}, goodbye!")
            sys.exit(0)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        # SIGHUP is not available on Windows
        if hasattr(signal, 'SIGHUP'):
            signal.signal(signal.SIGHUP, _handle_signal)
        # Ignore SIGPIPE to prevent silent process termination when writing to closed pipes
        # SIGPIPE is not available on Windows
        if hasattr(signal, 'SIGPIPE'):
            signal.signal(signal.SIGPIPE, signal.SIG_IGN)

        async def run_interactive():
            bus_task = asyncio.create_task(agent_loop.run())
            turn_done = asyncio.Event()
            turn_done.set()
            turn_response: list[str] = []

            async def _consume_outbound():
                while True:
                    try:
                        msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
                        if msg.metadata.get("_progress"):
                            is_tool_hint = msg.metadata.get("_tool_hint", False)
                            ch = agent_loop.channels_config
                            if ch and is_tool_hint and not ch.send_tool_hints:
                                pass
                            elif ch and not is_tool_hint and not ch.send_progress:
                                pass
                            else:
                                console.print(f"  [dim]↳ {msg.content}[/dim]")
                        elif not turn_done.is_set():
                            if msg.content:
                                turn_response.append(msg.content)
                            turn_done.set()
                        elif msg.content:
                            console.print()
                            _print_agent_response(msg.content, render_markdown=markdown)
                    except asyncio.TimeoutError:
                        continue
                    except asyncio.CancelledError:
                        break

            outbound_task = asyncio.create_task(_consume_outbound())

            try:
                while True:
                    try:
                        _flush_pending_tty_input()
                        user_input = await _read_interactive_input_async()
                        command = user_input.strip()
                        if not command:
                            continue

                        if _is_exit_command(command):
                            _restore_terminal()
                            console.print("\nGoodbye!")
                            break

                        turn_done.clear()
                        turn_response.clear()

                        await bus.publish_inbound(InboundMessage(
                            channel=cli_channel,
                            sender_id="user",
                            chat_id=cli_chat_id,
                            content=user_input,
                        ))

                        with _thinking_ctx():
                            await turn_done.wait()

                        if turn_response:
                            _print_agent_response(turn_response[0], render_markdown=markdown)
                    except KeyboardInterrupt:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
                    except EOFError:
                        _restore_terminal()
                        console.print("\nGoodbye!")
                        break
            finally:
                agent_loop.stop()
                outbound_task.cancel()
                await asyncio.gather(bus_task, outbound_task, return_exceptions=True)
                await agent_loop.close_mcp()
                _stop_background_process(privacy_proc)

        asyncio.run(run_interactive())


# ============================================================================
# Channel Commands
# ============================================================================


channels_app = typer.Typer(help="Manage channels")
app.add_typer(channels_app, name="channels")
whatsapp_contacts_app = typer.Typer(help="Manage WhatsApp direct reply targets (compatibility alias)")
channels_app.add_typer(whatsapp_contacts_app, name="whatsapp-contacts")
whatsapp_groups_app = typer.Typer(help="Manage WhatsApp group reply targets")
channels_app.add_typer(whatsapp_groups_app, name="whatsapp-groups")


@channels_app.command("status")
def channels_status():
    """Show channel status."""
    from nanobot.config.loader import load_config

    config = load_config()

    table = Table(title="Channel Status")
    table.add_column("Channel", style="cyan")
    table.add_column("Enabled", style="green")
    table.add_column("Configuration", style="yellow")

    # WhatsApp
    wa = config.channels.whatsapp
    table.add_row(
        "WhatsApp",
        "✓" if wa.enabled else "✗",
        wa.bridge_url
    )

    dc = config.channels.discord
    table.add_row(
        "Discord",
        "✓" if dc.enabled else "✗",
        dc.gateway_url
    )

    # Feishu
    fs = config.channels.feishu
    fs_config = f"app_id: {fs.app_id[:10]}..." if fs.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "Feishu",
        "✓" if fs.enabled else "✗",
        fs_config
    )

    # Mochat
    mc = config.channels.mochat
    mc_base = mc.base_url or "[dim]not configured[/dim]"
    table.add_row(
        "Mochat",
        "✓" if mc.enabled else "✗",
        mc_base
    )

    # Telegram
    tg = config.channels.telegram
    tg_config = f"token: {tg.token[:10]}..." if tg.token else "[dim]not configured[/dim]"
    table.add_row(
        "Telegram",
        "✓" if tg.enabled else "✗",
        tg_config
    )

    # Slack
    slack = config.channels.slack
    slack_config = "socket" if slack.app_token and slack.bot_token else "[dim]not configured[/dim]"
    table.add_row(
        "Slack",
        "✓" if slack.enabled else "✗",
        slack_config
    )

    # DingTalk
    dt = config.channels.dingtalk
    dt_config = f"client_id: {dt.client_id[:10]}..." if dt.client_id else "[dim]not configured[/dim]"
    table.add_row(
        "DingTalk",
        "✓" if dt.enabled else "✗",
        dt_config
    )

    # QQ
    qq = config.channels.qq
    qq_config = f"app_id: {qq.app_id[:10]}..." if qq.app_id else "[dim]not configured[/dim]"
    table.add_row(
        "QQ",
        "✓" if qq.enabled else "✗",
        qq_config
    )

    # Email
    em = config.channels.email
    em_config = em.imap_host if em.imap_host else "[dim]not configured[/dim]"
    table.add_row(
        "Email",
        "✓" if em.enabled else "✗",
        em_config
    )

    console.print(table)


def _get_bridge_dir() -> Path:
    """Get the bridge directory, setting it up if needed."""
    import shutil
    import subprocess

    # User's bridge location
    from nanobot.utils.paths import project_root
    user_bridge = project_root() / ".bridge-build"

    # Check for npm
    if not shutil.which("npm"):
        console.print("[red]npm not found. Please install Node.js >= 18.[/red]")
        raise typer.Exit(1)

    # Find source bridge: first check package data, then source dir
    pkg_bridge = Path(__file__).parent.parent / "bridge"  # nanobot/bridge (installed)
    src_bridge = Path(__file__).parent.parent.parent / "bridge"  # repo root/bridge (dev)

    source = None
    if (pkg_bridge / "package.json").exists():
        source = pkg_bridge
    elif (src_bridge / "package.json").exists():
        source = src_bridge

    if not source:
        console.print("[red]Bridge source not found.[/red]")
        console.print("Try reinstalling: pip install --force-reinstall nanobot")
        raise typer.Exit(1)

    # Reuse the cached bridge only when it exists and matches the current source.
    if (user_bridge / "dist" / "index.js").exists() and not _bridge_needs_refresh(source, user_bridge):
        return user_bridge

    console.print(f"{__logo__} Setting up bridge...")

    # Copy to user directory
    user_bridge.parent.mkdir(parents=True, exist_ok=True)
    if user_bridge.exists():
        shutil.rmtree(user_bridge)
    shutil.copytree(source, user_bridge, ignore=shutil.ignore_patterns("node_modules", "dist"))

    # Install and build
    try:
        console.print("  Installing dependencies...")
        subprocess.run(["npm", "install"], cwd=user_bridge, check=True, capture_output=True)

        console.print("  Building...")
        subprocess.run(["npm", "run", "build"], cwd=user_bridge, check=True, capture_output=True)

        console.print("[green]✓[/green] Bridge ready\n")
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Build failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)

    return user_bridge


def _bridge_needs_refresh(source: Path, cached: Path) -> bool:
    """Return True when the cached bridge should be recopied and rebuilt."""
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

    source_latest = latest_mtime(source)
    cached_latest = latest_mtime(cached)
    return source_latest > cached_latest


def _build_whatsapp_bridge_env(config: Config) -> dict[str, str]:
    """Build environment variables for the local WhatsApp bridge."""
    env = {**os.environ}
    wa = config.channels.whatsapp
    from nanobot.utils.paths import project_root
    _project_root = project_root()
    if wa.bridge_token:
        env["BRIDGE_TOKEN"] = wa.bridge_token
    if wa.web_browser_mode:
        env["WEB_BROWSER_MODE"] = wa.web_browser_mode
    if wa.web_cdp_url:
        env["WEB_CDP_URL"] = wa.web_cdp_url
    if wa.web_cdp_chrome_path:
        env["WEB_CDP_CHROME_PATH"] = wa.web_cdp_chrome_path
    if wa.web_profile_dir:
        env["WEB_PROFILE_DIR"] = wa.web_profile_dir
    env["AUTH_DIR"] = str(_project_root / "whatsapp-auth")

    parsed = urlparse(wa.bridge_url)
    if parsed.port:
        env["BRIDGE_PORT"] = str(parsed.port)
    return env


def _privacy_gateway_url(config: Config) -> str:
    """Return the local privacy gateway URL."""
    privacy = config.privacy_gateway
    return f"http://{privacy.listen_host}:{privacy.listen_port}/v1"


def _build_privacy_gateway_env(config: Config, upstream_base: str) -> dict[str, str]:
    """Build environment variables for the local privacy gateway."""
    # Privacy pipeline step 2a: serialize the privacy config so the child
    # gateway process can reconstruct the same runtime behavior.
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


def _ensure_whatsapp_bridge_browser(bridge_dir: Path, config: Config, env: dict[str, str]) -> None:
    """Install Playwright browser runtime when draft mode needs it."""
    import subprocess

    if config.channels.whatsapp.delivery_mode != "draft":
        return
    if config.channels.whatsapp.web_browser_mode != "launch":
        console.print("Using CDP browser mode for WhatsApp Web; skipping Playwright Chromium install")
        return

    console.print("Ensuring Playwright Chromium is installed for WhatsApp draft mode...")
    try:
        subprocess.run(
            ["npx", "playwright", "install", "chromium"],
            cwd=bridge_dir,
            check=True,
            env=env,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Playwright browser install failed: {e}[/red]")
        if e.stderr:
            console.print(f"[dim]{e.stderr.decode()[:500]}[/dim]")
        raise typer.Exit(1)


def _whatsapp_bridge_running(config: Config) -> bool:
    """Return True when the local WhatsApp bridge accepts a stable WebSocket connection."""
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
    else:
        parsed = urlparse(bridge_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 3001
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False


def _privacy_gateway_running(config: Config) -> bool:
    """Return True when the local privacy gateway accepts a stable health check."""
    gateway_url = _privacy_gateway_url(config).removesuffix("/v1") + "/healthz"

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
    else:
        parsed = urlparse(gateway_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 8787
        try:
            with socket.create_connection((host, port), timeout=0.3):
                return True
        except OSError:
            return False


def _start_whatsapp_bridge(config: Config):
    """Start the local WhatsApp bridge in the background when needed."""
    import subprocess

    if not config.channels.whatsapp.enabled:
        return None

    if _whatsapp_bridge_running(config):
        console.print("[green]✓[/green] WhatsApp bridge already running")
        return None

    bridge_dir = _get_bridge_dir()
    env = _build_whatsapp_bridge_env(config)
    _ensure_whatsapp_bridge_browser(bridge_dir, config, env)

    console.print(f"{__logo__} Starting WhatsApp bridge...")
    proc = subprocess.Popen(
        ["npm", "start"],
        cwd=bridge_dir,
        env=env,
        start_new_session=True,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            console.print(f"[red]WhatsApp bridge exited early with code {proc.returncode}[/red]")
            raise typer.Exit(1)
        if _whatsapp_bridge_running(config):
            console.print("[green]✓[/green] WhatsApp bridge ready")
            return proc
        time.sleep(0.2)

    console.print("[red]WhatsApp bridge did not become ready in time[/red]")
    _stop_whatsapp_bridge(proc)
    raise typer.Exit(1)


def _start_privacy_gateway(config: Config, upstream_base: str):
    """Start the local privacy gateway when custom cloud provider routing needs privacy filtering."""
    # Privacy pipeline step 2b: boot the local HTTP gateway as a sibling
    # process and wait until /healthz confirms it is ready to proxy traffic.
    import subprocess

    if not config.privacy_gateway.enabled:
        return None

    if _privacy_gateway_running(config):
        console.print("[green]✓[/green] Privacy gateway already running")
        return None

    env = _build_privacy_gateway_env(config, upstream_base)

    console.print("Starting privacy gateway...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "nanobot.privacy.gateway_server"],
        env=env,
        start_new_session=True,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            console.print(f"[red]Privacy gateway exited early with code {proc.returncode}[/red]")
            raise typer.Exit(1)
        if _privacy_gateway_running(config):
            console.print("[green]✓[/green] Privacy gateway ready")
            return proc
        time.sleep(0.2)

    console.print("[red]Privacy gateway did not become ready in time[/red]")
    _stop_background_process(proc)
    raise typer.Exit(1)


def _stop_whatsapp_bridge(proc) -> None:
    """Stop a background WhatsApp bridge process started by this CLI."""
    _stop_background_process(proc)


def _stop_background_process(proc) -> None:
    """Stop a background subprocess started by this CLI."""
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


def _get_whatsapp_reply_targets_file(config: Config) -> Path:
    """Return the WhatsApp reply-target registry path."""
    from nanobot.channels.whatsapp_reply_targets import reply_targets_path
    from nanobot.utils.paths import project_root

    return reply_targets_path(config.channels.whatsapp.reply_targets_file, project_root())


def _load_whatsapp_reply_targets_payload(config: Config) -> dict:
    """Load WhatsApp reply targets with legacy migrations applied once."""
    from nanobot.channels.whatsapp_reply_targets import load_reply_targets
    from nanobot.utils.paths import project_root

    return load_reply_targets(
        _get_whatsapp_reply_targets_file(config),
        project_root=project_root(),
        group_members_file=str(config.channels.whatsapp.group_members_file or ""),
    )


def _get_whatsapp_group_members_file(config: Config) -> Path | None:
    """Return the optional legacy WhatsApp group-members CSV path."""
    from nanobot.channels.whatsapp_group_members import group_members_path

    if not str(config.channels.whatsapp.group_members_file or "").strip():
        return None
    return group_members_path(config.channels.whatsapp.group_members_file)


def _sync_legacy_whatsapp_group_members(config: Config, payload: dict) -> Path | None:
    """Keep the optional legacy CSV in sync when explicitly configured."""
    from nanobot.channels.whatsapp_group_members import WhatsAppGroupMember, save_group_members

    legacy_path = _get_whatsapp_group_members_file(config)
    if legacy_path is None:
        return None

    rows: list[WhatsAppGroupMember] = []
    for raw in payload.get("group_reply_targets", []):
        if not isinstance(raw, dict):
            continue
        rows.append(
            WhatsAppGroupMember(
                group_id=str(raw.get("group_id", "") or "").strip(),
                group_name=str(raw.get("group_name", "") or "").strip(),
                member_id=str(raw.get("member_id", "") or "").strip(),
                member_pn=str(raw.get("member_phone", "") or "").strip(),
                member_label=str(raw.get("member_label", "") or "").strip(),
                enabled=bool(raw.get("enabled", True)),
            )
        )
    save_group_members(config.channels.whatsapp.group_members_file, rows)
    return legacy_path


@channels_app.command("login")
def channels_login():
    """Link device via QR code."""
    import subprocess

    from nanobot.config.loader import load_config

    config = load_config()
    bridge_dir = _get_bridge_dir()

    console.print(f"{__logo__} Starting bridge...")
    console.print("Scan the QR code to connect.\n")

    env = _build_whatsapp_bridge_env(config)
    _ensure_whatsapp_bridge_browser(bridge_dir, config, env)

    try:
        subprocess.run(["npm", "start"], cwd=bridge_dir, check=True, env=env)
    except subprocess.CalledProcessError as e:
        console.print(f"[red]Bridge failed: {e}[/red]")
    except FileNotFoundError:
        console.print("[red]npm not found. Please install Node.js.[/red]")


@channels_app.command("whatsapp-web")
def channels_whatsapp_web():
    """Explain that standalone WhatsApp Web CDP launch is disabled."""
    console.print(
        "[red]Standalone WhatsApp Web CDP launch is disabled. "
        "CDP is managed only during WhatsApp history parsing.[/red]"
    )
    raise typer.Exit(1)


@whatsapp_contacts_app.command("init")
def whatsapp_contacts_init():
    """Initialize the direct reply-target registry."""
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    payload = _load_whatsapp_reply_targets_payload(config)
    count = len(payload.get("direct_reply_targets", []))
    console.print(f"[green]✓[/green] WhatsApp direct reply-target store ready at {path} ({count} rows)")


@whatsapp_contacts_app.command("list")
def whatsapp_contacts_list():
    """List WhatsApp direct reply targets."""
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    payload = _load_whatsapp_reply_targets_payload(config)
    contacts = sorted(
        [
            row
            for row in payload.get("direct_reply_targets", [])
            if isinstance(row, dict)
        ],
        key=lambda row: str(row.get("phone", "")),
    )

    console.print(f"{__logo__} WhatsApp Direct Reply Targets\n")
    console.print(f"Store: {path}")
    if not contacts:
        console.print("[yellow]No WhatsApp direct reply targets configured[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Phone")
    table.add_column("Label")
    table.add_column("Push Name")
    table.add_column("Enabled")
    for contact in contacts:
        table.add_row(
            str(contact.get("phone", "") or "-"),
            str(contact.get("label", "") or "-"),
            str(contact.get("push_name", "") or "-"),
            "✓" if bool(contact.get("enabled", True)) else "✗",
        )
    console.print(table)


@whatsapp_contacts_app.command("add")
def whatsapp_contacts_add(
    phone: str = typer.Argument(..., help="Phone number to allow, e.g. +85212345678"),
    label: str = typer.Option("", "--label", "-l", help="Optional local label"),
):
    """Add one WhatsApp direct reply target."""
    from nanobot.channels.whatsapp_reply_targets import upsert_direct_reply_target
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    try:
        row = upsert_direct_reply_target(path, phone=phone, label=label, enabled=True)
    except ValueError:
        console.print("[red]Invalid phone number[/red]")
        raise typer.Exit(1) from None
    console.print(
        f"[green]✓[/green] Added WhatsApp direct reply target {row['phone']} to {path}"
    )


@whatsapp_contacts_app.command("remove")
def whatsapp_contacts_remove(
    phone: str = typer.Argument(..., help="Phone number to remove"),
):
    """Remove one WhatsApp direct reply target."""
    from nanobot.channels.whatsapp_reply_targets import remove_direct_reply_target
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    if not remove_direct_reply_target(path, phone=phone):
        console.print(f"[yellow]Direct reply target not found: {phone}[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] Removed WhatsApp direct reply target {phone} from {path}")


@whatsapp_groups_app.command("init")
def whatsapp_groups_init():
    """Initialize the group reply-target registry."""
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    payload = _load_whatsapp_reply_targets_payload(config)
    count = len(payload.get("group_reply_targets", []))
    legacy_path = _sync_legacy_whatsapp_group_members(config, payload)
    console.print(f"[green]✓[/green] WhatsApp group reply-target store ready at {path} ({count} rows)")
    if legacy_path is not None:
        console.print(f"[dim]Legacy CSV compatibility store synced at {legacy_path}[/dim]")


@whatsapp_groups_app.command("list")
def whatsapp_groups_list():
    """List WhatsApp group reply targets."""
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    payload = _load_whatsapp_reply_targets_payload(config)
    rows = [
        row
        for row in payload.get("group_reply_targets", [])
        if isinstance(row, dict)
    ]

    console.print(f"{__logo__} WhatsApp Group Reply Targets\n")
    console.print(f"Store: {path}")
    if not rows:
        console.print("[yellow]No WhatsApp group reply targets configured[/yellow]")
        return

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Group ID")
    table.add_column("Group Name")
    table.add_column("Member ID")
    table.add_column("Phone")
    table.add_column("Label")
    table.add_column("Enabled")
    for row in rows:
        table.add_row(
            str(row.get("group_id", "") or "-"),
            str(row.get("group_name", "") or "-"),
            str(row.get("member_id", "") or "-"),
            str(row.get("member_phone", "") or "-"),
            str(row.get("member_label", "") or "-"),
            "✓" if bool(row.get("enabled", True)) else "✗",
        )
    console.print(table)


@whatsapp_groups_app.command("add")
def whatsapp_groups_add(
    group_id: str = typer.Option("", "--group-id", help="WhatsApp group ID, optional for bootstrap rows"),
    group_name: str = typer.Option("", "--group-name", help="Group name for matching or bootstrap"),
    member_id: str = typer.Option("", "--member-id", help="Exact group member ID, e.g. 123456789@lid"),
    member_pn: str = typer.Option("", "--member-pn", help="Member phone number, e.g. +85212345678"),
    label: str = typer.Option("", "--label", "-l", help="Optional local label"),
):
    """Add one WhatsApp group reply target."""
    from nanobot.channels.whatsapp_reply_targets import upsert_group_reply_target
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    try:
        upsert_group_reply_target(
            path,
            group_id=group_id,
            group_name=group_name,
            member_id=member_id,
            member_phone=member_pn,
            member_label=label,
            enabled=True,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    legacy_path = _sync_legacy_whatsapp_group_members(config, _load_whatsapp_reply_targets_payload(config))
    console.print(f"[green]✓[/green] Added WhatsApp group reply target to {path}")
    if legacy_path is not None:
        console.print(f"[dim]Legacy CSV compatibility store synced at {legacy_path}[/dim]")


@whatsapp_groups_app.command("remove")
def whatsapp_groups_remove(
    group_id: str = typer.Argument("", help="WhatsApp group ID"),
    group_name: str = typer.Option("", "--group-name", help="Group name for bootstrap rows"),
    member_id: str = typer.Option("", "--member-id", help="Exact group member ID to remove"),
    member_pn: str = typer.Option("", "--member-pn", help="Member phone number to remove"),
):
    """Remove one WhatsApp group reply target."""
    from nanobot.channels.whatsapp_reply_targets import remove_group_reply_target
    from nanobot.config.loader import load_config

    config = load_config()
    path = _get_whatsapp_reply_targets_file(config)
    if not remove_group_reply_target(
        path,
        group_id=group_id,
        group_name=group_name,
        member_id=member_id,
        member_phone=member_pn,
    ):
        console.print("[yellow]Group reply target not found[/yellow]")
        raise typer.Exit(1)
    legacy_path = _sync_legacy_whatsapp_group_members(config, _load_whatsapp_reply_targets_payload(config))
    console.print(f"[green]✓[/green] Removed WhatsApp group reply target from {path}")
    if legacy_path is not None:
        console.print(f"[dim]Legacy CSV compatibility store synced at {legacy_path}[/dim]")


# ============================================================================
# Status Commands
# ============================================================================


@app.command()
def status():
    """Show nanobot status."""
    from nanobot.config.loader import get_config_path, load_config
    from nanobot.utils.paths import is_inside_project, project_root

    config_path = get_config_path()
    config = load_config()
    workspace = config.workspace_path
    root = project_root()

    console.print(f"{__logo__} nanobot Status\n")

    # --- Path confinement summary ---
    def _path_tag(p: Path) -> str:
        if is_inside_project(p):
            return "[green]✓ (project-local)[/green]"
        return "[yellow]⚠ (external)[/yellow]"

    console.print(f"[bold]Project root:[/bold]  {root}")
    console.print(f"Config:         {config_path} {_path_tag(config_path)}")
    console.print(f"Workspace:      {workspace} {_path_tag(workspace)}")
    console.print(f"Sessions:       {workspace / 'sessions'} {_path_tag(workspace / 'sessions')}")
    console.print(f"State/journal:  {workspace / 'state'} {_path_tag(workspace / 'state')}")
    console.print(f"Memory:         {workspace / 'memory'} {_path_tag(workspace / 'memory')}")
    console.print(f"Data:           {root / 'data'} {_path_tag(root / 'data')}")
    console.print(f"Auth:           {root / 'whatsapp-auth'} {_path_tag(root / 'whatsapp-auth')}")
    wa_profile = Path(config.channels.whatsapp.web_profile_dir)
    console.print(f"Browser:        {wa_profile} {_path_tag(wa_profile)}")
    console.print()

    if config_path.exists():
        from nanobot.providers.registry import PROVIDERS

        console.print(f"Model: {config.agents.defaults.model}")

        # Check API keys from registry
        for spec in PROVIDERS:
            p = getattr(config.providers, spec.name, None)
            if p is None:
                continue
            if spec.is_oauth:
                console.print(f"{spec.label}: [green]✓ (OAuth)[/green]")
            elif spec.is_local:
                # Local deployments show api_base instead of api_key
                if p.api_base:
                    console.print(f"{spec.label}: [green]✓ {p.api_base}[/green]")
                else:
                    console.print(f"{spec.label}: [dim]not set[/dim]")
            else:
                has_key = bool(p.api_key)
                console.print(f"{spec.label}: {'[green]✓[/green]' if has_key else '[dim]not set[/dim]'}")


# ============================================================================
# OAuth Login
# ============================================================================

provider_app = typer.Typer(help="Manage providers")
app.add_typer(provider_app, name="provider")


_LOGIN_HANDLERS: dict[str, callable] = {}


def _register_login(name: str):
    def decorator(fn):
        _LOGIN_HANDLERS[name] = fn
        return fn
    return decorator


@provider_app.command("login")
def provider_login(
    provider: str = typer.Argument(..., help="OAuth provider (e.g. 'openai-codex', 'github-copilot')"),
):
    """Authenticate with an OAuth provider."""
    from nanobot.providers.registry import PROVIDERS

    key = provider.replace("-", "_")
    spec = next((s for s in PROVIDERS if s.name == key and s.is_oauth), None)
    if not spec:
        names = ", ".join(s.name.replace("_", "-") for s in PROVIDERS if s.is_oauth)
        console.print(f"[red]Unknown OAuth provider: {provider}[/red]  Supported: {names}")
        raise typer.Exit(1)

    handler = _LOGIN_HANDLERS.get(spec.name)
    if not handler:
        console.print(f"[red]Login not implemented for {spec.label}[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} OAuth Login - {spec.label}\n")
    handler()


@_register_login("openai_codex")
def _login_openai_codex() -> None:
    try:
        from oauth_cli_kit import get_token, login_oauth_interactive
        token = None
        try:
            token = get_token()
        except Exception:
            pass
        if not (token and token.access):
            console.print("[cyan]Starting interactive OAuth login...[/cyan]\n")
            token = login_oauth_interactive(
                print_fn=lambda s: console.print(s),
                prompt_fn=lambda s: typer.prompt(s),
            )
        if not (token and token.access):
            console.print("[red]✗ Authentication failed[/red]")
            raise typer.Exit(1)
        console.print(f"[green]✓ Authenticated with OpenAI Codex[/green]  [dim]{token.account_id}[/dim]")
    except ImportError:
        console.print("[red]oauth_cli_kit not installed. Run: pip install oauth-cli-kit[/red]")
        raise typer.Exit(1)


@_register_login("github_copilot")
def _login_github_copilot() -> None:
    import asyncio

    console.print("[cyan]Starting GitHub Copilot device flow...[/cyan]\n")

    async def _trigger():
        from litellm import acompletion
        await acompletion(model="github_copilot/gpt-4o", messages=[{"role": "user", "content": "hi"}], max_tokens=1)

    try:
        asyncio.run(_trigger())
        console.print("[green]✓ Authenticated with GitHub Copilot[/green]")
    except Exception as e:
        console.print(f"[red]Authentication error: {e}[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
