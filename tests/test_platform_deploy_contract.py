from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
DEPLOY = ROOT / "scripts" / "deploy-platform.sh"


def test_platform_deploy_preserves_runtime_credentials_and_backups() -> None:
    script = DEPLOY.read_text(encoding="utf-8")
    assert "/srv/hs-data-platform" in script
    assert '[[ "$TARGET_ROOT" != "/"' in script
    assert '"$SOURCE_ROOT/postgres/docker-compose.yml"' in script
    assert '"$SOURCE_ROOT/postgres/.gitignore"' in script
    assert "postgres/docker.env" not in script
    assert "postgres/connection.json" not in script
    assert 'rsync -a --delete "$SOURCE_ROOT/$entry/"' in script
