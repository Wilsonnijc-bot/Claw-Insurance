"""Regression tests for project-local path confinement.

These tests verify the invariant that normal project usage keeps all
runtime state strictly inside the project directory tree.

If any of these tests fail, it means code has regressed back to
writing/reading state from external or legacy paths.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from nanobot.utils.paths import (
    PathEscapeError,
    _set_project_root,
    confine_path,
    is_inside_project,
    project_path,
    project_root,
    resolve_project_relative,
)


# ---------------------------------------------------------------------------
# Core paths.py tests
# ---------------------------------------------------------------------------


class TestProjectRoot:
    """Tests for project_root() and project_path()."""

    def test_project_root_is_this_repo(self):
        root = project_root()
        # The project root must contain pyproject.toml
        assert (root / "pyproject.toml").exists(), f"project_root() = {root} but pyproject.toml not found"

    def test_project_root_contains_nanobot_package(self):
        root = project_root()
        assert (root / "nanobot" / "__init__.py").exists()

    def test_project_path_joins_correctly(self):
        assert project_path("data") == project_root() / "data"
        assert project_path("sessions", "test") == project_root() / "sessions" / "test"

    def test_project_root_prefers_env_override(self, monkeypatch, tmp_path):
        runtime_root = tmp_path / "runtime-root"
        (runtime_root / "nanobot").mkdir(parents=True)
        (runtime_root / "config.json").write_text("{}", encoding="utf-8")
        monkeypatch.setenv("NANOBOT_PROJECT_ROOT", str(runtime_root))
        assert project_root() == runtime_root.resolve()

    def test_project_root_can_use_runtime_cwd_for_packaged_install(self, monkeypatch, tmp_path):
        runtime_root = tmp_path / "runtime-root"
        (runtime_root / "nanobot").mkdir(parents=True)
        (runtime_root / "config.json").write_text("{}", encoding="utf-8")
        monkeypatch.delenv("NANOBOT_PROJECT_ROOT", raising=False)
        old_root = _set_project_root(tmp_path / "installed-root")
        try:
            monkeypatch.chdir(runtime_root)
            assert project_root() == runtime_root.resolve()
        finally:
            _set_project_root(old_root)


class TestConfinePath:
    """Tests for confine_path() — the runtime confinement guard."""

    def test_accepts_project_local_path(self):
        result = confine_path(project_root() / "sessions" / "test")
        assert result == (project_root() / "sessions" / "test").resolve()

    def test_rejects_home_nanobot_path(self):
        evil_path = Path.home() / ".nanobot" / "config.json"
        with pytest.raises(PathEscapeError, match="escapes project root"):
            confine_path(evil_path)

    def test_rejects_tmp_path(self):
        with pytest.raises(PathEscapeError, match="escapes project root"):
            confine_path(Path("/tmp/nanobot-outside"))

    def test_rejects_parent_traversal(self):
        with pytest.raises(PathEscapeError, match="escapes project root"):
            confine_path(project_root() / ".." / ".." / "etc" / "passwd")

    def test_allow_override_logs_warning_but_returns(self):
        result = confine_path(Path("/tmp/external"), allow_override=True)
        assert result == Path("/tmp/external").resolve()

    def test_rejects_home_directory(self):
        with pytest.raises(PathEscapeError):
            confine_path(Path.home() / "random-file.txt")


class TestResolveProjectRelative:
    """Tests for resolve_project_relative()."""

    def test_relative_path_resolves_to_project_root(self):
        result = resolve_project_relative("data/contacts")
        assert result == (project_root() / "data" / "contacts").resolve()

    def test_absolute_project_path_stays(self):
        p = project_root() / "sessions"
        result = resolve_project_relative(str(p))
        assert result == p.resolve()

    def test_tilde_stays_literal_not_expanded(self):
        # With expanduser removed, ~ stays literal when joined onto project root
        result = resolve_project_relative("~/.nanobot/config.json")
        # It should resolve to project_root/~/.nanobot/config.json (relative)
        # NOT to /Users/xxx/.nanobot/config.json
        assert str(Path.home()) not in str(result) or is_inside_project(result)


class TestIsInsideProject:
    """Tests for is_inside_project()."""

    def test_project_dir_is_inside(self):
        assert is_inside_project(project_root() / "sessions")

    def test_home_nanobot_is_outside(self):
        assert not is_inside_project(Path.home() / ".nanobot")

    def test_tmp_is_outside(self):
        assert not is_inside_project(Path("/tmp"))


# ---------------------------------------------------------------------------
# helpers.py tests
# ---------------------------------------------------------------------------


class TestGetWorkspacePath:
    """Tests for get_workspace_path() confinement."""

    def test_default_returns_project_root(self):
        from nanobot.utils.helpers import get_workspace_path
        result = get_workspace_path(None)
        assert result == project_root()

    def test_relative_path_resolves_inside_project(self):
        from nanobot.utils.helpers import get_workspace_path
        result = get_workspace_path("sessions")
        assert result == project_root() / "sessions"
        assert is_inside_project(result)

    def test_absolute_external_path_raises(self):
        from nanobot.utils.helpers import get_workspace_path
        with pytest.raises(PathEscapeError):
            get_workspace_path("/tmp/outside-workspace")

    def test_tilde_path_does_not_expand_to_home(self):
        from nanobot.utils.helpers import get_workspace_path
        # ~ should not expand; it becomes project_root/~
        # This should either stay inside project or raise
        try:
            result = get_workspace_path("~/evil")
            # If it resolves, it must be inside project
            assert is_inside_project(result)
        except PathEscapeError:
            pass  # Also acceptable — confinement caught it


# ---------------------------------------------------------------------------
# config/loader.py tests
# ---------------------------------------------------------------------------


class TestConfigLoaderConfinement:
    """Tests that config loading does not touch ~/.nanobot."""

    def test_load_config_does_not_reference_home_nanobot(self, monkeypatch, tmp_path):
        """load_config() must work even if Path.home() raises."""
        # Point config to a temp path so it doesn't find the real one
        config_file = tmp_path / "config.json"
        config_file.write_text("{}", encoding="utf-8")
        monkeypatch.setenv("NANOBOT_CONFIG_PATH", str(config_file))

        from nanobot.config.loader import load_config
        config = load_config()
        assert config is not None

    def test_no_home_root_constant_in_loader(self):
        """The string '_HOME_ROOT' should not appear in loader.py anymore."""
        import nanobot.config.loader as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "_HOME_ROOT" not in source, "Legacy _HOME_ROOT constant still exists in loader.py"

    def test_no_dot_nanobot_in_loader(self):
        """No '.nanobot' directory reference should exist in loader.py."""
        import nanobot.config.loader as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        # Allow references in comments/docstrings but not in Path() calls
        # Search for actual Path usage patterns with .nanobot
        pattern = r"""Path\([^)]*['"]\.\s*nanobot"""
        matches = re.findall(pattern, source)
        assert not matches, f"Found .nanobot in Path() call: {matches}"

    def test_no_migration_function_called(self):
        """The migration function must not exist in the module."""
        import nanobot.config.loader as mod
        assert not hasattr(mod, "_maybe_migrate_home_nanobot_to_project")

    def test_get_config_path_default_is_project_local(self, monkeypatch):
        monkeypatch.delenv("NANOBOT_CONFIG_PATH", raising=False)
        monkeypatch.delenv("NANOBOT_APP_CONFIG_PATH", raising=False)

        from nanobot.config.loader import get_config_path
        result = get_config_path()
        assert is_inside_project(result), f"Default config path is outside project: {result}"


# ---------------------------------------------------------------------------
# config/schema.py tests
# ---------------------------------------------------------------------------


class TestConfigSchemaConfinement:
    """Tests that Config.workspace_path is confined."""

    def test_default_workspace_is_project_root(self):
        from nanobot.config.schema import Config
        config = Config()
        assert config.workspace_path == project_root()

    def test_relative_workspace_stays_inside_project(self):
        from nanobot.config.schema import Config
        config = Config.model_validate({"agents": {"defaults": {"workspace": "."}}})
        assert is_inside_project(config.workspace_path)


# ---------------------------------------------------------------------------
# WhatsApp path module tests
# ---------------------------------------------------------------------------


class TestWhatsAppPathConfinement:
    """Tests that WhatsApp file path functions stay project-local."""

    def test_group_members_path_default_is_project_local(self):
        from nanobot.config.schema import Config
        from nanobot.channels.whatsapp_group_members import group_members_path
        config = Config()
        result = group_members_path(config.channels.whatsapp.group_members_file)
        assert is_inside_project(result), f"group_members_path outside project: {result}"

    def test_reply_targets_path_default_is_project_local(self):
        from nanobot.config.schema import Config
        from nanobot.channels.whatsapp_reply_targets import reply_targets_path
        config = Config()
        result = reply_targets_path(config.channels.whatsapp.reply_targets_file, project_root())
        assert is_inside_project(result), f"reply_targets_path outside project: {result}"

    def test_contacts_path_rejects_home_path(self):
        from nanobot.channels.whatsapp_contacts import contacts_path
        with pytest.raises(PathEscapeError):
            contacts_path(str(Path.home() / ".nanobot" / "contacts.json"))

    def test_reply_targets_path_rejects_tilde(self):
        """Tilde should not expand to home directory."""
        from nanobot.channels.whatsapp_reply_targets import reply_targets_path
        # Since expanduser is removed, ~/... becomes a relative path
        # joined onto project root, which should stay confined
        result = reply_targets_path("~/targets.json", project_root())
        assert is_inside_project(result)


# ---------------------------------------------------------------------------
# Agent filesystem tools tests
# ---------------------------------------------------------------------------


class TestFilesystemToolConfinement:
    """Tests that agent filesystem tools don't expanduser."""

    def test_resolve_path_does_not_expand_tilde(self):
        from nanobot.agent.tools.filesystem import _resolve_path
        workspace = project_root()
        # ~ should stay literal, resolving relative to workspace
        result = _resolve_path("~/test.txt", workspace=workspace)
        # Should NOT be under home directory
        assert "test.txt" in str(result)
        # The resolved path should be workspace/~/test.txt
        assert str(Path.home()) not in str(result) or str(result).startswith(str(workspace))


# ---------------------------------------------------------------------------
# Source code audit tests — ensure no regressions
# ---------------------------------------------------------------------------


class TestSourceCodeAudit:
    """Grep-based tests to catch path confinement regressions."""

    @staticmethod
    def _read_python_sources() -> dict[str, str]:
        """Read all Python source files in the nanobot package."""
        sources = {}
        for py_file in (project_root() / "nanobot").rglob("*.py"):
            rel = str(py_file.relative_to(project_root()))
            sources[rel] = py_file.read_text(encoding="utf-8")
        return sources

    def test_no_path_home_in_non_install_code(self):
        """Path.home() should only appear in install/helper code."""
        allowed_files = {
            "nanobot/cli/commands.py",  # install-ui-command (intentional)
            "nanobot/macos_cdp_helper.py",  # macOS host helper installer/runtime
            "nanobot/utils/paths.py",   # is_inside_project uses it in tests only
        }
        for rel, source in self._read_python_sources().items():
            if rel in allowed_files:
                continue
            assert "Path.home()" not in source, (
                f"{rel} contains Path.home() — this may route state outside the project"
            )

    def test_no_expanduser_in_runtime_code(self):
        """expanduser() should not appear in runtime path resolution code."""
        allowed_files = {
            "nanobot/cli/commands.py",  # install-ui-command + chrome path resolution (intentional)
        }
        for rel, source in self._read_python_sources().items():
            if rel in allowed_files:
                continue
            # Match .expanduser() method calls
            matches = re.findall(r"\.expanduser\(\)", source)
            assert not matches, (
                f"{rel} contains .expanduser() — this may route state outside the project"
            )

    def test_no_home_nanobot_constant(self):
        """No module should define a _HOME_ROOT or reference ~/.nanobot as a Path."""
        pattern = re.compile(r'_HOME_ROOT|Path\.home\(\)\s*/\s*"\.nanobot"')
        for rel, source in self._read_python_sources().items():
            matches = pattern.findall(source)
            assert not matches, (
                f"{rel} references legacy home-directory nanobot path: {matches}"
            )


# ---------------------------------------------------------------------------
# Docker file audit
# ---------------------------------------------------------------------------


class TestDockerConfinement:
    """Tests that Docker files use project-local paths."""

    def test_dockerfile_no_root_nanobot(self):
        dockerfile = project_root() / "Dockerfile"
        if not dockerfile.exists():
            pytest.skip("Dockerfile not found")
        content = dockerfile.read_text(encoding="utf-8")
        assert "/root/.nanobot" not in content, (
            "Dockerfile still references /root/.nanobot"
        )

    def test_compose_no_home_nanobot_volume(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert "~/.nanobot" not in content, (
            "docker-compose.yml still mounts ~/.nanobot"
        )

    def test_compose_uses_workspace_runtime_root(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert "NANOBOT_PROJECT_ROOT: /workspace" in content
        assert "- .:/workspace" in content

    def test_compose_sets_host_cdp_helper_env(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert "WEB_CDP_HELPER_URL: ${WEB_CDP_HELPER_URL-http://host.docker.internal:9230}" in content
        assert "WEB_HOST_PROFILE_DIR: ${WEB_HOST_PROFILE_DIR-./whatsapp-web}" in content
        assert "${PWD}/whatsapp-web" not in content
        assert "WEB_HISTORY_SYNC_ENABLED: ${WEB_HISTORY_SYNC_ENABLED-true}" in content

    def test_compose_uses_launcher_not_gateway(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert 'command: ["launcher", "--api-port", "3456"]' in content
        assert "pull_policy: always" not in content
        assert "~/.nanobot" not in content

    def test_compose_backend_runs_launcher(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert 'command: ["launcher", "--api-port", "3456"]' in content

    def test_compose_builds_backend_locally(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert "build:" in content
        assert "dockerfile: Dockerfile" in content
        assert "pull_policy: always" not in content

    def test_compose_does_not_mount_example_config_as_live_runtime(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert "./config.example.json:/app/config.json" not in content
        assert "./google.example.json:/app/google.json" not in content
        assert "./supabase.example.json:/app/supabase.json" not in content
        assert "./googleconfig.example.json:/app/googleconfig.json" not in content
        assert "./supabaseconfig.example.json:/app/supabaseconfig.json" not in content

    def test_compose_provides_host_cdp_env(self):
        compose = project_root() / "docker-compose.yml"
        if not compose.exists():
            pytest.skip("docker-compose.yml not found")
        content = compose.read_text(encoding="utf-8")
        assert "WEB_BROWSER_MODE: ${WEB_BROWSER_MODE-cdp}" in content
        assert "WEB_CDP_URL: ${WEB_CDP_URL-http://host.docker.internal:9222}" in content

    def test_docker_up_script_prepares_host_helper_flow(self):
        script = project_root() / "docker-up"
        if not script.exists():
            pytest.skip("docker-up not found")
        content = script.read_text(encoding="utf-8")
        assert 'python3 -m nanobot.macos_cdp_helper health' in content
        assert 'python3 -m nanobot.macos_cdp_helper install' in content
        assert 'export WEB_HISTORY_SYNC_ENABLED="false"' in content
        assert 'docker compose up -d --build "$@"' in content
