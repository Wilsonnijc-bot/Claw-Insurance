"""Utility functions for nanobot."""

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from nanobot.utils.paths import confine_path, project_root


def detect_image_mime(data: bytes) -> str | None:
    """Detect image MIME type from magic bytes, ignoring file extension."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def ensure_dir(path: Path) -> Path:
    """Ensure directory exists, return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """Project-local data directory."""
    return ensure_dir(project_root())


def get_workspace_path(workspace: str | None = None) -> Path:
    """Resolve and ensure workspace path. Defaults to the project root.

    Workspace must resolve inside the project root (path confinement).
    The legacy ``expanduser()`` call has been removed — tilde is no longer
    expanded so paths cannot silently escape to the home directory.
    """
    if workspace:
        path = Path(workspace)
        if not path.is_absolute():
            path = project_root() / path
        # Enforce confinement — workspace cannot escape the project tree
        path = confine_path(path)
    else:
        path = project_root()
    return ensure_dir(path)


def timestamp() -> str:
    """Current ISO timestamp."""
    return datetime.now().isoformat()


_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')

def safe_filename(name: str) -> str:
    """Replace unsafe path characters with underscores."""
    return _UNSAFE_CHARS.sub("_", name).strip()


def readable_session_bundle_name(key: str) -> str:
    """Return a normalized readable bundle folder name for a session key."""
    text = str(key or "").strip()
    if not text:
        return "session__unknown"

    if not text.startswith("whatsapp:"):
        return safe_filename(text.replace(":", "__"))

    parts = text.split(":")
    if len(parts) == 2:
        identity = str(parts[1] or "").strip()
        digits = "".join(ch for ch in identity if ch.isdigit())
        if digits:
            return safe_filename(f"whatsapp__{digits}")
        return safe_filename(text.replace(":", "__"))

    if len(parts) == 3:
        group_id = str(parts[1] or "").strip()
        member_identity = str(parts[2] or "").strip()
        member_digits = "".join(ch for ch in member_identity if ch.isdigit())
        return safe_filename(f"whatsapp__{group_id}__{member_digits or member_identity}")

    return safe_filename(text.replace(":", "__"))


def split_message(content: str, max_len: int = 2000) -> list[str]:
    """
    Split content into chunks within max_len, preferring line breaks.

    Args:
        content: The text content to split.
        max_len: Maximum length per chunk (default 2000 for Discord compatibility).

    Returns:
        List of message chunks, each within max_len.
    """
    if not content:
        return []
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    while content:
        if len(content) <= max_len:
            chunks.append(content)
            break
        cut = content[:max_len]
        # Try to break at newline first, then space, then hard break
        pos = cut.rfind('\n')
        if pos <= 0:
            pos = cut.rfind(' ')
        if pos <= 0:
            pos = max_len
        chunks.append(content[:pos])
        content = content[pos:].lstrip()
    return chunks


def _template_root() -> Any | None:
    from importlib.resources import files as pkg_files

    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return None
    if not tpl.is_dir():
        return None
    return tpl


def load_shipped_template(name: str) -> str | None:
    """Return shipped template content by filename when available."""
    tpl = _template_root()
    if tpl is None:
        return None
    path = tpl / name
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def sync_workspace_templates(workspace: Path, silent: bool = False) -> list[str]:
    """Create mutable workspace scaffolding from bundled templates when missing."""
    tpl = _template_root()
    if tpl is None:
        return []

    added: list[str] = []

    def _write(src, dest: Path):
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")
        added.append(str(dest.relative_to(workspace)))

    # Prompt/persona files now load directly from bundled shipped templates.
    # Only create mutable workspace-local files here.
    heartbeat = tpl / "HEARTBEAT.md"
    if heartbeat.is_file():
        _write(heartbeat, workspace / "HEARTBEAT.md")
    # Per-client memory dirs are created on-demand by MemoryStore.
    # Only create the global knowledge file placeholder here.
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "GLOBAL.md")
    _write(None, workspace / "memory" / "HISTORY.md")
    (workspace / "skills").mkdir(exist_ok=True)

    if added and not silent:
        from rich.console import Console
        for name in added:
            Console().print(f"  [dim]Created {name}[/dim]")
    return added
