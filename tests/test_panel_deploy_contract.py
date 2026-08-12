from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[1]
DEPLOY = ROOT / "scripts" / "deploy-panel.sh"
UNIT = ROOT / "panel" / "systemd" / "kolodahs-sync@.service"


def test_panel_deploy_keeps_secrets_and_data_outside_releases() -> None:
    script = DEPLOY.read_text(encoding="utf-8")
    assert "/srv/api-kolodahearthstone/panel" in script
    assert "/srv/api-kolodahearthstone/panel-data" in script
    assert "/etc/api-kolodahearthstone/panel-config.php" in script
    assert "--exclude='config.php'" in script
    assert "--exclude='uploads/'" in script
    assert "--exclude='var/'" in script
    assert '[[ "$path" != "/"' in script
    assert '[[ "$EUID" -eq 0 ]]' in script
    assert "chown -R koloda:koloda \"$DATA_ROOT\"" not in script
    assert 'find "$RELEASE_ROOT/assets" -type f -exec chmod 0644' in script


def test_sync_unit_uses_canonical_panel_runtime() -> None:
    unit = UNIT.read_text(encoding="utf-8")
    assert "/srv/api-kolodahearthstone/panel/current" in unit
    assert "/var/www/koloda/data/www/db.kolodahs.ru" not in unit
