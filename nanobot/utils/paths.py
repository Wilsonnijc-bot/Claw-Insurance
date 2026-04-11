"""Centralised project-root path resolution and confinement.

Every module that needs to resolve runtime state paths must import from here
instead of computing its own ``Path(__file__).resolve().parents[N]``.

The path confinement invariant:

    Normal project usage must stay fully project-local.  All runtime state —
    config, sessions, logs, auth, browser profiles, contacts, reply targets,
    journals, and caches — resolves inside this repository's directory tree.

    The only intentional exceptions are:
    • ``install-ui-command`` writes wrapper scripts to a global bin directory.
    • ``NANOBOT_CONFIG_PATH`` env var can explicitly point to an external config.

``confine_path()`` enforces this at runtime.  Any resolved path outside the
project root raises ``ValueError`` unless deliberately overridden.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton project root
# ---------------------------------------------------------------------------

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


def _set_project_root(root: Path) -> Path:
    """Override the project root (for tests only). Returns the previous root."""
    global _PROJECT_ROOT
    old = _PROJECT_ROOT
    _PROJECT_ROOT = root.resolve()
    return old


def _looks_like_runtime_root(path: Path) -> bool:
    """Return True when *path* looks like a project/runtime root."""
    return (
        (path / "nanobot").is_dir()
        and (
            (path / "pyproject.toml").exists()
            or (path / "config.json").exists()
            or (path / "googleconfig.json").exists()
            or (path / "supabaseconfig.json").exists()
        )
    )


def _runtime_project_root() -> Path:
    """Resolve the effective project root for the active runtime."""
    override = os.environ.get("NANOBOT_PROJECT_ROOT")
    if override:
        return Path(override).resolve()

    cwd = Path.cwd().resolve()
    if _looks_like_runtime_root(cwd):
        return cwd

    return _PROJECT_ROOT


def project_root() -> Path:
    """Return the absolute path to this project's root directory."""
    return _runtime_project_root()


def project_path(*parts: str) -> Path:
    """Join *parts* onto the project root and return the result."""
    return project_root().joinpath(*parts)


def project_path_str(*parts: str) -> str:
    """Join *parts* onto the project root and return as a string."""
    return str(project_path(*parts))


# ---------------------------------------------------------------------------
# Path confinement guard
# ---------------------------------------------------------------------------


class PathEscapeError(ValueError):
    """Raised when a resolved path escapes the project root."""


# When True, confine_path() skips the check (for tests that use tmp_path).
_CONFINEMENT_DISABLED: bool = False


def confine_path(candidate: Path | str, *, allow_override: bool = False) -> Path:
    """Resolve *candidate* and verify it lives inside the project root.

    Parameters
    ----------
    candidate:
        A ``Path`` or string to resolve.
    allow_override:
        When ``True``, log a warning instead of raising when the path
        escapes the project root.  Use *only* for the documented
        env-var config override.

    Returns
    -------
    Path
        The resolved, confined path.

    Raises
    ------
    PathEscapeError
        If the resolved path is outside the project root and
        *allow_override* is ``False``.
    """
    resolved = Path(candidate).resolve()
    if _CONFINEMENT_DISABLED:
        return resolved
    root = project_root()
    try:
        resolved.relative_to(root)
    except ValueError:
        if allow_override:
            logger.warning(
                "Path is outside project root (explicit override): %s", resolved
            )
            return resolved
        raise PathEscapeError(
            f"Path escapes project root: {resolved}  "
            f"(project root: {root})"
        )
    return resolved


def resolve_project_relative(raw: str | Path) -> Path:
    """Resolve a potentially-relative path string against the project root.

    • Relative paths (no leading ``/``) are joined onto the project root.
    • Absolute paths are returned as-is (but NOT confined — call
      ``confine_path()`` separately if you need the guard).
    • ``~`` / ``expanduser`` is **not** applied.  Tilde stays literal so it
      cannot silently route to home-directory locations.
    """
    p = Path(raw) if isinstance(raw, str) else raw
    if not p.is_absolute():
        return (project_root() / p).resolve()
    return p.resolve()


def is_inside_project(path: Path | str) -> bool:
    """Return ``True`` when the resolved *path* is inside the project root."""
    root = project_root()
    try:
        Path(path).resolve().relative_to(root)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Convenience: runtime directory helpers
# ---------------------------------------------------------------------------

_RUNTIME_DIRS = (
    "data",
    "sessions",
    "state",
    "memory",
    "whatsapp-auth",
    "whatsapp-web",
    "whatsapp-web-debug",
    "media",
    "cron",
    "skills",
)


def ensure_runtime_dirs() -> None:
    """Create all expected project-local runtime directories."""
    root = project_root()
    for name in _RUNTIME_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)
