from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_release_compose_pulls_two_versioned_images_without_source_mount() -> None:
    content = (ROOT / "compose.release.yml").read_text(encoding="utf-8")

    assert "${BACKEND_IMAGE:-hendrickyan/claw-insurance-backend}:v${CLAW_VERSION:?" in content
    assert "${FRONTEND_IMAGE:-hendrickyan/claw-insurance-frontend}:v${CLAW_VERSION:?" in content
    assert "pull_policy: always" in content
    assert "build:" not in content
    assert ".:/workspace" not in content
    assert "./config.json:/app/config.json:ro" in content
    assert "./config.example.json:/app/config.json" not in content


def test_application_release_version_has_one_source_of_truth() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert version == "1.0.0"
    assert f"CLAW_VERSION={version}" in env_example


def test_customer_setup_scripts_pull_without_building_source() -> None:
    for relative in (
        "scripts/setup-windows.ps1",
        "scripts/setup-macos.sh",
        "scripts/setup-linux.sh",
    ):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert "compose.release.yml pull" in content
        assert "--build" not in content


def test_multiarch_publish_script_builds_both_platforms_and_images() -> None:
    content = (ROOT / "scripts" / "publish-multiarch.ps1").read_text(encoding="utf-8")

    assert "linux/amd64,linux/arm64" in content
    assert "claw-insurance-backend" in content
    assert "claw-insurance-frontend" in content
    assert content.count("--push") >= 2


def test_docker_image_declares_prebuilt_bridge() -> None:
    content = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "ENV NANOBOT_PREBUILT_BRIDGE_DIR=/app/bridge" in content
    assert "COPY pyproject.toml uv.lock" in content
    assert "uv sync --frozen --no-dev" in content
    assert "RUN npm ci && npm run build && npm prune --omit=dev" in content


def test_docker_context_excludes_customer_secrets_and_runtime_data() -> None:
    content = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    for ignored in ("config.json", "google.json", "supabase.json", "secrets/", "runtime/"):
        assert ignored in content
