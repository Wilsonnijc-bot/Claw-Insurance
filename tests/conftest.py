"""Shared pytest fixtures for the nanobot test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auto_confine_tmp(request, monkeypatch):
    """When a test uses ``tmp_path``, disable the path-confinement guard.

    Tests that create files in the OS temp directory would otherwise hit
    ``PathEscapeError``.  Disabling the guard keeps the real project root
    intact (so skills, config, etc. are still discoverable) while allowing
    workspace and data paths to live in ``tmp_path``.

    The *real* confinement logic is tested in ``test_path_confinement.py``
    which deliberately does **not** use ``tmp_path``.
    """
    if "tmp_path" not in request.fixturenames:
        return
    from nanobot.utils import paths
    monkeypatch.setattr(paths, "_CONFINEMENT_DISABLED", True)
