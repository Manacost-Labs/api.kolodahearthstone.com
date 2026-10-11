import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("history_stage", ROOT / "scripts/stage-card-history-runtime.py")
staging = importlib.util.module_from_spec(spec)
spec.loader.exec_module(staging)


def test_candidate_preserves_newer_runtime_and_private_symlinks(tmp_path):
    runtime = tmp_path / "releases" / "current-release"
    runtime.mkdir(parents=True)
    for name in ["config.php", "uploads", "var"]:
        (runtime / name).symlink_to(tmp_path / ("private-" + name))
    (runtime / "api").mkdir()
    (runtime / "scripts").mkdir()
    (runtime / "api/index.php").write_text("<?php\ndeclare(strict_types=1);\nKEEP_TILES_API_116\n        'endpoints' => [\n    if ($path === '/api/v1/cards') {}\n")
    (runtime / "next-data.php").write_text("['analytics_registry']\nif ($action === 'session') {}\nKEEP_NEW_ADAPTER\n")
    (runtime / "scripts/scan_cards.php").write_text("KEEP_ABERRATION_MAPPING\nWHERE premium.card_image IS NOT NULL AND premium.card_image <> ''\n")
    (runtime / "scripts/sync_constructed_cards.py").write_text('from pymysql.cursors import DictCursor\nKEEP_BLACK_EMPIRE_METADATA\ndef normalize_card():\n    fallback_render = (None)\n    payload = {\n        "patch_image_override": patch_image_override,\n    }\n')
    target = runtime.parent / "candidate"
    result = staging.stage(ROOT / "panel", runtime, target)
    assert result["activated"] is False
    for name in ["config.php", "uploads", "var"]:
        assert (target / name).is_symlink()
        assert (target / name).readlink() == (runtime / name).readlink()
    assert "KEEP_BLACK_EMPIRE_METADATA" in (target / "scripts/sync_constructed_cards.py").read_text()
    assert "KEEP_ABERRATION_MAPPING" in (target / "scripts/scan_cards.php").read_text()
    assert "KEEP_TILES_API_116" in (target / "api/index.php").read_text()
    assert "card-history" in (target / "api/index.php").read_text()
    assert "bartender-skins" in (target / "api/index.php").read_text()
    assert "card_history" not in (runtime / "next-data.php").read_text()
    with pytest.raises(ValueError, match="new release"):
        staging.stage(ROOT / "panel", runtime, target)
