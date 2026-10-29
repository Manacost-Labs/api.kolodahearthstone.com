from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docker_build_cannot_hide_browser_install_failure() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "python -m patchright install chromium" in dockerfile
    assert "patchright install chromium || true" not in dockerfile


def test_runtime_image_has_healthcheck_and_server_command() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "HEALTHCHECK" in dockerfile
    assert "http://127.0.0.1:8000/health" in dockerfile
    assert 'CMD ["python", "-m", "app.server"]' in dockerfile

