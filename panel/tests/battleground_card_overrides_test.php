<?php
declare(strict_types=1);

require dirname(__DIR__) . '/scripts/lib/battleground_card_overrides.php';

// Load the real import upsert without its entrypoint, which opens production connections.
$scanSource = file_get_contents(dirname(__DIR__) . '/scripts/scan_cards.php');
foreach (['should_replace_image', 'record_change', 'upsert_card'] as $function) {
    if (!preg_match('/^function ' . $function . '\\([^\n]*\n\\{.*?^\\}/ms', $scanSource, $match)) {
        throw new RuntimeException('Cannot load ' . $function);
    }
    eval($match[0]);
}

function check_override(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

// Pure rules.
check_override(bg_card_override_normalize('attack', '0') === 0, 'Zero stats must stay zero');
check_override(bg_card_override_normalize('tavern_tier', '') === null, 'Empty numbers mean no value');
check_override(bg_card_override_normalize('in_pool', '1') === 1 && bg_card_override_normalize('in_pool', null) === 0, 'Flags normalize to 0/1');
check_override(bg_card_override_normalize('name_en', '  ') === null, 'Blank text means no value');
check_override(bg_card_override_normalize('notes', "A\r\nB\r") === "A\nB", 'Textarea CRLF must not count as an edit');

$current = ['card_id' => 'BG_TEST', 'name' => 'Мурлок-развдчик', 'name_en' => null, 'attack' => 2, 'in_pool' => '1', 'notes' => "Текст\nМеханики: X"];
$submitted = ['card_id' => 'BG_TEST', 'name' => 'Мурлок-разведчик', 'name_en' => '', 'attack' => 2, 'in_pool' => 1, 'notes' => "Текст\r\nМеханики: X", 'card_image' => '/uploads/x.png'];
$changes = bg_card_override_changes($current, $submitted, []);
check_override(array_keys($changes['upsert']) === ['name'], 'Only the edited field becomes a correction');
check_override($changes['upsert']['name'] === ['manual' => 'Мурлок-разведчик', 'upstream' => 'Мурлок-развдчик'], 'A correction remembers the replaced upstream value');
check_override($changes['delete'] === [] && array_keys($changes['edits']) === ['name'], 'Unchanged fields stay untouched');

$stored = ['name' => ['manual_value' => '"Мурлок-разведчик"', 'upstream_value' => '"Мурлок-развдчик"']];
$second = bg_card_override_changes(['name' => 'Мурлок-разведчик'], ['name' => 'Мурлок-следопыт'], $stored);
check_override($second['upsert']['name']['upstream'] === 'Мурлок-развдчик', 'Repeated edits keep the original upstream baseline');
$revert = bg_card_override_changes(['name' => 'Мурлок-разведчик'], ['name' => 'Мурлок-развдчик'], $stored);
check_override($revert['delete'] === ['name'] && $revert['upsert'] === [], 'Typing the upstream value back removes the correction');

$kept = bg_card_override_apply(['name' => 'Мурлок-развдчик', 'attack' => 2], $stored + ['image' => ['manual_value' => '"x"', 'upstream_value' => '"y"']]);
check_override($kept['card']['name'] === 'Мурлок-разведчик' && $kept['kept'] === ['name'], 'Unchanged upstream keeps the manual value');
check_override(!isset($kept['card']['image']), 'Unknown fields are never written');
$settled = bg_card_override_apply(['name' => 'Мурлок-разведчик'], $stored);
check_override($settled['settled'] === ['name'] && $settled['kept'] === [], 'Upstream catching up settles the correction');
$released = bg_card_override_apply(['name' => 'Новое имя'], $stored);
check_override($released['card']['name'] === 'Новое имя' && isset($released['released']['name']), 'A newer upstream value wins and is reported');

// Database round trip through the panel edit and the real import upsert.
$pdo = new PDO('sqlite::memory:', null, null, [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]);
$pdo->sqliteCreateFunction('IF', fn($condition, $then, $else) => $condition ? $then : $else, 3);
$pdo->exec('CREATE TABLE battlegrounds_cards (
    id INTEGER PRIMARY KEY, name TEXT, name_en TEXT, card_id TEXT UNIQUE, dbf INT, card_type TEXT, variant_kind TEXT,
    base_dbf INT, base_card_id TEXT, premium_dbf INT, tavern_tier INT, creature_type TEXT, attack INT, health INT,
    in_pool INT, duos_only INT, card_image TEXT, golden_image TEXT, art_image TEXT, framed_image TEXT, notes TEXT,
    source TEXT, source_hash TEXT, first_seen_at TEXT, last_seen_at TEXT, changed_at TEXT)');
$pdo->exec('CREATE TABLE battlegrounds_card_overrides (
    card_id TEXT NOT NULL, field_name TEXT NOT NULL, manual_value TEXT, upstream_value TEXT,
    created_by TEXT NOT NULL DEFAULT "", created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (card_id, field_name))');
$pdo->exec('CREATE TABLE battlegrounds_card_changes (
    id INTEGER PRIMARY KEY, card_id TEXT, source TEXT, old_hash TEXT, new_hash TEXT, change_type TEXT, payload_json TEXT)');

function imported_card(array $changes = []): array
{
    $card = $changes + [
        'name' => 'Мурлок-развдчик', 'name_en' => 'Murloc Scout', 'card_id' => 'BG_TEST', 'dbf' => 1, 'card_type' => 'minion',
        'variant_kind' => 'base', 'base_dbf' => null, 'base_card_id' => null, 'premium_dbf' => null, 'tavern_tier' => 1,
        'creature_type' => 'murloc', 'attack' => 2, 'health' => 1, 'in_pool' => 1, 'duos_only' => 0,
        'card_image' => 'https://example.test/card.png', 'art_image' => null, 'framed_image' => null,
        'notes' => "Текст\nМеханики: X", 'source' => 'hearthstonejson', 'source_payload' => ['v' => 1],
    ];
    $card['source_hash'] = hash('sha256', json_encode([$card['name'], $card['attack']], JSON_UNESCAPED_UNICODE));

    return $card;
}

function stored_card(PDO $pdo): array
{
    return $pdo->query("SELECT * FROM battlegrounds_cards WHERE card_id = 'BG_TEST'")->fetch();
}

function import_card(PDO $pdo, array $card): void
{
    upsert_card($pdo, $card, false, bg_card_overrides_load_all($pdo)[$card['card_id']] ?? []);
}

function panel_edit(PDO $pdo, array $fields): array
{
    $current = stored_card($pdo);
    $submitted = $fields + $current;
    $changes = bg_card_override_changes($current, $submitted, bg_card_overrides_load($pdo, 'BG_TEST'));
    $set = implode(', ', array_map(fn($field) => "$field = :$field", array_keys($fields)));
    $pdo->prepare("UPDATE battlegrounds_cards SET $set WHERE card_id = 'BG_TEST'")->execute($fields);
    bg_card_overrides_record_edit($pdo, 'BG_TEST', 'BG_TEST', $changes, 'Zulut30');

    return $changes;
}

function change_types(PDO $pdo): array
{
    return $pdo->query('SELECT change_type FROM battlegrounds_card_changes ORDER BY id')->fetchAll(PDO::FETCH_COLUMN);
}

upsert_card($pdo, imported_card(), false);
panel_edit($pdo, ['name' => 'Мурлок-разведчик', 'notes' => "Текст\r\nМеханики: X"]);
check_override(array_keys(bg_card_overrides_load($pdo, 'BG_TEST')) === ['name'], 'The panel stores only the real correction');
$audit = json_decode($pdo->query("SELECT payload_json FROM battlegrounds_card_changes WHERE change_type = 'manual_edit'")->fetchColumn(), true);
check_override($audit['author'] === 'Zulut30' && $audit['fields']['name']['to'] === 'Мурлок-разведчик', 'Every panel edit is audited with its author');

import_card($pdo, imported_card());
check_override(stored_card($pdo)['name'] === 'Мурлок-разведчик', 'Re-import must keep the manual correction');
check_override(stored_card($pdo)['notes'] === "Текст\nМеханики: X", 'Untouched fields still follow the import');

import_card($pdo, imported_card(['name' => 'Мурлок-разведчик']));
check_override(bg_card_overrides_load($pdo, 'BG_TEST') === [], 'A settled correction is removed');

panel_edit($pdo, ['attack' => 3]);
import_card($pdo, imported_card(['name' => 'Мурлок-разведчик', 'attack' => 4]));
check_override((int)stored_card($pdo)['attack'] === 4, 'A newer game patch wins over an old correction');
check_override(bg_card_overrides_load($pdo, 'BG_TEST') === [], 'A released correction is removed');
check_override(change_types($pdo) === ['new', 'manual_edit', 'changed', 'manual_settled', 'manual_edit', 'changed', 'manual_released'], 'Every override transition reaches the change log');

panel_edit($pdo, ['health' => 5]);
bg_card_overrides_record_edit($pdo, 'BG_TEST', 'BG_RENAMED', ['upsert' => [], 'delete' => [], 'edits' => []], 'Zulut30');
check_override(array_keys(bg_card_overrides_load($pdo, 'BG_RENAMED')) === ['health'] && bg_card_overrides_load($pdo, 'BG_TEST') === [], 'Renaming a card keeps its corrections');

echo "Battleground card override tests passed\n";
