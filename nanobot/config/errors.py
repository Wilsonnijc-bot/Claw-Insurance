"""Shared configuration layout errors."""

from __future__ import annotations


class ConfigLayoutError(ValueError):
    """Raised when config files use an unsupported layout or filename."""
