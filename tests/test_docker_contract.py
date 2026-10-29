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


def test_runtime_image_installs_fingerprint_node_dependencies() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    runtime = dockerfile[dockerfile.index("FROM python:") :]

    # The runtime image only runs `node`; `npm ci` lives in a separate stage
    # (the Debian `npm` package pulls ~350 node-* packages into apt).
    assert "nodejs" in runtime
    assert "npm" not in runtime.split("COPY requirements.txt")[0]
    assert "FROM node:18-bookworm-slim AS fingerprint-node" in dockerfile
    assert "scripts/fingerprint-node/package-lock.json" in dockerfile
    assert "npm ci --omit=dev --ignore-scripts" in dockerfile.split("FROM python:")[0]
    assert (
        "COPY --from=fingerprint-node /build/node_modules "
        "/app/scripts/fingerprint-node/node_modules"
    ) in runtime
