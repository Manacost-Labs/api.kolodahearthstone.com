#!/usr/bin/env python3
"""Prepare a guarded overlay; never activate releases or modify databases."""

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path


def once(text: str, old: str, new: str) -> str:
    if text.count(old) != 1:
        raise ValueError("Runtime anchor differs; discard candidate and review again")
    return text.replace(old, new, 1)


def function_block(text: str, start: str, end: str) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError("Reviewed function boundaries differ")
    first = text.index(start)
    return text[first : text.index(end, first)]


def scanner_overlay(runtime: str, reviewed: str) -> str:
    membership = function_block(
        reviewed, "/** HSJ retains the original expansion", "function normalize_card("
    )
    runtime = once(
        runtime, "function normalize_card(", membership + "function normalize_card("
    )
    runtime = once(
        runtime,
        "if (($ru['set'] ?? null) !== 'BATTLEGROUNDS') {",
        "if (!is_battlegrounds_catalog_card($ru, $en)) {",
    )
    base_link = function_block(
        reviewed, "function resolve_bg_base_card_id(", "function upsert_card("
    )
    runtime = once(
        runtime, "function upsert_card(", base_link + "function upsert_card("
    )
    runtime = once(
        runtime,
        "function upsert_card(PDO $pdo, array $card, bool $dryRun = false): array\n{",
        "function upsert_card(PDO $pdo, array $card, bool $dryRun = false): array\n{\n    $card['base_card_id'] = resolve_bg_base_card_id($pdo, $card);",
    )
    return once(
        runtime,
        "$source = cli_option('source', 'all');",
        "if (defined('KOLODA_SCAN_LIBRARY_ONLY') && KOLODA_SCAN_LIBRARY_ONLY) return;\n\n$source = cli_option('source', 'all');",
    )


def storage_overlay(runtime: str) -> str:
    if "record_catalog_metadata" in runtime:
        raise ValueError(
            "Storage metadata hook already exists; review instead of duplicating"
        )
    return once(
        runtime,
        "    tmp.replace(path)\n    _match_parent_permissions(path)\n",
        "    written = tmp.stat()\n    tmp.replace(path)\n    _match_parent_permissions(path)\n    from .catalog_metadata import record_catalog_metadata\n\n    record_catalog_metadata(path, payload, expected_file=(written.st_ino, written.st_size, written.st_mtime_ns))\n",
    )


def telemetry_overlay(runtime: str) -> str:
    for old, new in (
        ("SLO_TARGET_RATE_PCT = 99.0", "SLO_TARGET_RATE_PCT = 99.9"),
        (
            "elif good_attempts / eligible_attempts >= SLO_TARGET_RATE_PCT / 100.0:",
            "elif _ratio_meets_target(good_attempts, eligible_attempts):",
        ),
        (
            '"allowed_bad_attempts": round(allowed_bad, 2)',
            '"allowed_bad_attempts": round(allowed_bad, 6)',
        ),
        (
            '"error_budget_remaining_attempts": round(remaining, 2)',
            '"error_budget_remaining_attempts": round(remaining, 6)',
        ),
    ):
        runtime = once(runtime, old, new)
    return runtime


def shadow_overlay(runtime: str, reviewed: str) -> str:
    start = "    $radarSql = runProcess("
    fragment = function_block(reviewed, start, "    $mergeOutput = runProcess(")
    if runtime.count("\nCOMMIT;\n") != 1:
        raise ValueError("Shadow merge transaction differs")
    return once(
        runtime,
        "    $mergeOutput = runProcess(",
        fragment + "    $mergeOutput = runProcess(",
    )


def stage(args):
    source, target = args.source.resolve(), args.target.absolute()
    runtime = args.php_runtime.resolve()
    if target.exists() or target.is_symlink() or target.name == "current":
        raise ValueError("Use a new candidate directory")
    for name in ("config.php", "uploads", "var"):
        if not (runtime / name).is_symlink():
            raise ValueError("Private config and data must remain symlinks")
    system = args.api_system.read_bytes()
    if (
        hashlib.sha256(system).hexdigest()
        != "49dc1abbd10cb687e29f38a879e4244b00811f24d4932d197dc51a688d27477c"
    ):
        raise ValueError("Active API router changed; review overlay again")
    # Validate every narrow patch before creating any candidate files.
    scanner = scanner_overlay(
        (runtime / "scripts/scan_cards.php").read_text(),
        (source / "panel/scripts/scan_cards.php").read_text(),
    )
    storage = storage_overlay(args.api_storage.read_text())
    telemetry = telemetry_overlay(args.api_telemetry.read_text())
    shadow = shadow_overlay(
        args.platform_sync.read_text(),
        (source / "platform/scripts/sync-shadow.php").read_text(),
    )
    php = target / "panel"
    shutil.copytree(
        runtime,
        php,
        symlinks=True,
        ignore=shutil.ignore_patterns(".env", ".env.*", "__pycache__", "*.pyc"),
    )
    for name in (
        "lib/card_history.php",
        "scripts/archive_card_history_media.py",
        "scripts/repair_missing_bg_variants.php",
    ):
        shutil.copy2(source / "panel" / name, php / name)
    (php / "scripts/scan_cards.php").write_text(scanner)
    default = runtime.stat()
    for path in [php, *php.rglob("*")]:
        if path.is_symlink():
            continue
        original = runtime / path.relative_to(php)
        owner = (
            original.stat()
            if original.exists() and not original.is_symlink()
            else default
        )
        os.chown(path, owner.st_uid, owner.st_gid)
    api = target / "api" / "app"
    (api / "routers").mkdir(parents=True)
    for name in (
        "catalog_metadata.py",
        "source_catalog.py",
        "source_completeness.py",
        "source_diagnostics.py",
    ):
        shutil.copy2(source / "app" / name, api / name)
    shutil.copy2(source / "app/routers/system.py", api / "routers/system.py")
    (api / "storage.py").write_text(storage)
    (api / "reliability_telemetry.py").write_text(telemetry)
    platform = target / "platform"
    (platform / "scripts").mkdir(parents=True)
    (platform / "scripts/sync-shadow.php").write_text(shadow)
    for name in (
        "scripts/radar-shadow.py",
        "sql/013_hsguru_deck_radar.sql",
        "sql/014_card_version_media.sql",
        "systemd/kolodahs-card-history-media.service",
        "systemd/kolodahs-card-history-media.timer",
    ):
        (platform / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "platform" / name, platform / name)
    return {
        "candidate": str(target),
        "activated": False,
        "database_modified": False,
        "php_parent": str(runtime),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in (
        "source",
        "php-runtime",
        "api-system",
        "api-storage",
        "api-telemetry",
        "platform-sync",
        "target",
    ):
        parser.add_argument("--" + option, type=Path, required=True)
    print(json.dumps(stage(parser.parse_args())))
