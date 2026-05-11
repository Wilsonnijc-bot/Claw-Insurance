"""Canonical client identity model for strict per-client data isolation.

Invariant: client-scoped operations must never access another client's
conversation data.  Every data read/write path resolves through a
``ClientKey`` so that phone formatting differences, stale push_name
values, and display-name collisions can never cause cross-client leakage.
"""

from __future__ import annotations

import re
from pathlib import Path


class CrossClientError(Exception):
    """Raised when an operation would access data belonging to a different client."""


_DIGITS_ONLY = re.compile(r"\D")


class ClientKey:
    """Immutable, normalised client identity based on phone digits.

    All session, memory, and data paths are derived from this key so that
    two clients can never share a namespace regardless of how their raw
    phone string was formatted upstream.

    Construction:
        >>> key = ClientKey.normalize("+852-6842-4658")
        >>> key.phone          # "85268424658"
        >>> key.session_key    # "whatsapp:85268424658"
    """

    __slots__ = ("_phone",)

    def __init__(self, phone: str) -> None:
        if not phone or not phone.strip():
            raise ValueError("ClientKey requires a non-empty normalised phone string")
        self._phone = phone.strip()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def normalize(cls, raw: str) -> "ClientKey":
        """Build a ClientKey from any phone-like string.

        Strips ``+``, ``-``, spaces, ``@s.whatsapp.net`` suffixes, and all
        other non-digit characters.  Raises ``ValueError`` when no digits
        remain.
        """
        text = str(raw or "").strip()
        # Strip JID suffix if present
        if "@" in text:
            text = text.split("@", 1)[0]
        digits = _DIGITS_ONLY.sub("", text)
        if not digits:
            raise ValueError(f"Cannot normalise phone to ClientKey: {raw!r}")
        return cls(digits)

    @classmethod
    def try_normalize(cls, raw: str) -> "ClientKey | None":
        """Like :meth:`normalize` but returns ``None`` instead of raising."""
        try:
            return cls.normalize(raw)
        except ValueError:
            return None

    # ------------------------------------------------------------------
    # Derived paths / keys
    # ------------------------------------------------------------------

    @property
    def phone(self) -> str:
        """Digits-only canonical phone."""
        return self._phone

    @property
    def session_key(self) -> str:
        """Session key used by :class:`SessionManager`."""
        return f"whatsapp:{self._phone}"

    @property
    def bundle_dir_name(self) -> str:
        """Name of the per-client session bundle directory."""
        return f"whatsapp__{self._phone}"

    def memory_dir(self, workspace: Path) -> Path:
        """Per-client memory directory: ``<workspace>/memory/<phone>/``."""
        return workspace / "memory" / self._phone

    # ------------------------------------------------------------------
    # Comparison / hashing
    # ------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ClientKey):
            return self._phone == other._phone
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        if isinstance(other, ClientKey):
            return self._phone != other._phone
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._phone)

    def __repr__(self) -> str:
        return f"ClientKey({self._phone!r})"

    def __str__(self) -> str:
        return self._phone

    # ------------------------------------------------------------------
    # Guard helpers
    # ------------------------------------------------------------------

    @staticmethod
    def assert_same_client(a: "ClientKey", b: "ClientKey") -> None:
        """Raise :class:`CrossClientError` when *a* and *b* differ."""
        if a != b:
            raise CrossClientError(
                f"Cross-client access blocked: {a!r} != {b!r}"
            )

    @classmethod
    def from_session_key(cls, session_key: str) -> "ClientKey":
        """Extract a ``ClientKey`` from a ``whatsapp:<phone>`` session key.

        For group session keys (``whatsapp:<group>:<phone>``) the *member*
        phone is used.  Raises ``ValueError`` for non-WhatsApp keys.
        """
        text = str(session_key or "").strip()
        if not text.startswith("whatsapp:"):
            raise ValueError(f"Not a WhatsApp session key: {text!r}")
        parts = text.split(":", 2)
        # Direct: whatsapp:<phone>
        if len(parts) == 2:
            return cls.normalize(parts[1])
        # Group: whatsapp:<group_id>:<member_phone>
        if len(parts) == 3:
            return cls.normalize(parts[2])
        raise ValueError(f"Malformed WhatsApp session key: {text!r}")
