"""Runtime overlays must preserve unrelated live improvements and fail closed."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "chat_overlay", Path(__file__).parents[1] / "scripts/stage-chat-gaps-runtime.py"
)
overlay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(overlay)


def test_storage_keeps_live_lab_ingestion():
    live = "    tmp.replace(path)\n    _match_parent_permissions(path)\n\ndef save_status():\n    ingest_published(source_id, payload)\n"
    patched = overlay.storage_overlay(live)
    assert "record_catalog_metadata" in patched
    assert "ingest_published(source_id, payload)" in patched
    with pytest.raises(ValueError):
        overlay.storage_overlay(patched)


def test_scanner_preserves_live_art_recipes():
    reviewed = (Path(__file__).parents[1] / "panel/scripts/scan_cards.php").read_text()
    live = "LIVE_ART_RECIPE\nfunction normalize_card(array $ru, array $en = []): ?array\n{\n    if (($ru['set'] ?? null) !== 'BATTLEGROUNDS') {\n        return null;\n    }\n}\nfunction upsert_card(PDO $pdo, array $card, bool $dryRun = false): array\n{\n}\n$source = cli_option('source', 'all');\n"
    patched = overlay.scanner_overlay(live, reviewed)
    assert patched.startswith("LIVE_ART_RECIPE\n")
    assert "is_battlegrounds_catalog_card($ru, $en)" in patched
    assert "$card['base_card_id'] = resolve_bg_base_card_id($pdo, $card);" in patched
    assert "KOLODA_SCAN_LIBRARY_ONLY" in patched
    with pytest.raises(ValueError):
        overlay.scanner_overlay(patched, reviewed)


def test_shadow_keeps_live_incremental_merges():
    reviewed = (
        Path(__file__).parents[1] / "platform/scripts/sync-shadow.php"
    ).read_text()
    live = "INCREMENTAL_MERGE\nCOMMIT;\n    $mergeOutput = runProcess(\n"
    patched = overlay.shadow_overlay(live, reviewed)
    assert patched.startswith("INCREMENTAL_MERGE\n")
    assert "radar-shadow.py" in patched
    assert patched.count("$mergeOutput = runProcess(") == 1
    with pytest.raises(ValueError):
        overlay.shadow_overlay(live.replace("COMMIT;", "DIFFERENT;"), reviewed)


def test_telemetry_preserves_other_live_methods():
    live = (Path(__file__).parents[1] / "app/reliability_telemetry.py").read_text()
    for new, old in [
        ("SLO_TARGET_RATE_PCT = 99.9", "SLO_TARGET_RATE_PCT = 99.0"),
        (
            "elif _ratio_meets_target(good_attempts, eligible_attempts):",
            "elif good_attempts / eligible_attempts >= SLO_TARGET_RATE_PCT / 100.0:",
        ),
        (
            '"allowed_bad_attempts": round(allowed_bad, 6)',
            '"allowed_bad_attempts": round(allowed_bad, 2)',
        ),
        (
            '"error_budget_remaining_attempts": round(remaining, 6)',
            '"error_budget_remaining_attempts": round(remaining, 2)',
        ),
    ]:
        live = live.replace(new, old)
    patched = overlay.telemetry_overlay(live + "\n# LIVE_FEATURE\n")
    assert patched.endswith("# LIVE_FEATURE\n")
    assert "SLO_TARGET_RATE_PCT = 99.9" in patched
