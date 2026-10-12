#!/usr/bin/env python3
"""Stage a narrow history overlay from the active PHP release; never activate it."""
import argparse
import json
import os
from pathlib import Path
import shutil

FILES = [
    'lib/card_history.php', 'scripts/card_history_schema.py',
    'scripts/install_card_history.py', 'scripts/sync_battlegrounds_images.py',
    'scripts/catalog_card_text.py', 'scripts/catalog_integrity.py',
    'scripts/reconcile_card_catalog.py', 'scripts/wiki_http.py', 'lib/bartender_catalog.php',
]


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Runtime anchor differs; do not deploy this candidate')
    return text.replace(old, new, 1)


def stage(source, runtime, target):
    runtime = runtime.resolve()
    if target.exists() or target.is_symlink() or target.name == 'current':
        raise ValueError('Use a new release directory')
    if target.parent.resolve() != runtime.parent.resolve():
        raise ValueError('Candidate must share the runtime releases directory')
    for name in ['config.php', 'uploads', 'var']:
        if not (runtime / name).is_symlink():
            raise ValueError('Preserve private config and runtime-data symlinks')
    shutil.copytree(runtime, target, symlinks=True,
                    ignore=shutil.ignore_patterns('.env', '.env.*', '__pycache__', '*.pyc'))
    for name in FILES:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, target / name)

    path = target / 'api/index.php'
    text = path.read_text()
    desired = (source / 'api/index.php').read_text()
    start = desired.index("    if (preg_match('~^/api/v1/card-history/card/")
    end = desired.index("    if ($path === '/api/v1/cards')", start)
    text = once(text, "declare(strict_types=1);", "declare(strict_types=1);\nrequire_once __DIR__ . '/../lib/card_history.php';\nrequire_once __DIR__ . '/../lib/bartender_catalog.php';")
    text = once(text, "    if ($path === '/api/v1/cards')", desired[start:end] + "    if ($path === '/api/v1/cards')")
    text = once(text, "        'endpoints' => [", "        'endpoints' => [\n            'GET /api/v1/bartender-skins' => 'Скины бармена.',\n            'GET /api/v1/bartender-skins/{card_id}' => 'Скин бармена по card_id.',\n            'GET /api/v1/card-history/{entity}/{card_id}' => 'Сохранённые версии карты.',\n            'GET /api/v1/card-history/card/{card_id}/legacy' => 'Исходный журнал Полей сражений.',")
    path.write_text(text)

    path = target / 'next-data.php'
    text = path.read_text()
    desired = (source / 'next-data.php').read_text()
    start = desired.index("if ($action === 'card_history')")
    end = desired.index("if ($action === 'session')", start)
    text = once(text, "'analytics_registry']", "'analytics_registry', 'card_history']")
    text = once(text, "if ($action === 'session')", desired[start:end] + "if ($action === 'session')")
    path.write_text(text)

    path = target / 'scripts/scan_cards.php'
    text = path.read_text()
    text = once(text, "WHERE premium.card_image IS NOT NULL AND premium.card_image <> ''",
                "WHERE premium.card_image IS NOT NULL AND premium.card_image <> ''\n              AND (base_card.golden_image IS NULL OR base_card.golden_image NOT LIKE '/uploads/golden/%')")
    path.write_text(text)
    path = target / 'scripts/sync_constructed_cards.py'
    text = path.read_text()
    text = once(text, 'from pymysql.cursors import DictCursor\n',
                'from pymysql.cursors import DictCursor\nfrom catalog_card_text import repair_texts\n')
    text = once(text, '    fallback_render = (',
                '    text_ru, text_en, text_repair = repair_texts(\n'
                '        card_id, text_ru, text_en, (hsj_ru or {}).get("text"), (hsj_en or {}).get("text"),\n'
                '    )\n    fallback_render = (')
    text = once(text, '        "patch_image_override": patch_image_override,',
                '        **({"text_repair": text_repair} if text_repair else {}),\n        "patch_image_override": patch_image_override,')
    path.write_text(text)
    preserve_runtime_ownership(runtime, target)
    return {'runtime': str(runtime), 'candidate': str(target), 'activated': False,
            'changed_files': FILES + ['api/index.php', 'next-data.php', 'scripts/scan_cards.php', 'scripts/sync_constructed_cards.py']}


def preserve_runtime_ownership(runtime, target):
    """copytree preserves modes, but deployment as root must also restore owners.

    Do not follow or modify private config, upload and session symlink targets.
    New source files use the owner of the existing API entry point.
    """
    default = (runtime / 'api/index.php').stat()
    for path in [target, *target.rglob('*')]:
        if path.is_symlink():
            continue
        original = runtime / path.relative_to(target)
        owner = original.stat() if original.exists() and not original.is_symlink() else default
        os.chown(path, owner.st_uid, owner.st_gid)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--target', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.runtime, args.target)))
