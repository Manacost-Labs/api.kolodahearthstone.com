<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/battleground_creature_types.php';

// Load only the pure functions: the entrypoints otherwise open production DB connections.
foreach ([
    'scripts/scan_cards.php' => 'map_creature_type',
    'api/index.php' => 'creature_types',
] as $file => $function) {
    $source = file_get_contents(dirname(__DIR__) . '/' . $file);
    if (!preg_match('/^function ' . $function . '\\([^\n]*\n\\{.*?^\\}/ms', $source, $match)) {
        throw new RuntimeException('Cannot load ' . $function);
    }
    eval($match[0]);
}

$cases = [
    [['race' => 'ABERRATION'], 'aberration'],
    [['races' => ['ABERRATION']], 'aberration'],
    [['race' => 'aberration'], 'aberration'],
    [['race' => 'UNKNOWN', 'races' => ['ABERRATION']], 'aberration'],
    [['race' => 'MECHANICAL'], 'mech'],
    [['races' => ['BEAST', 'DRAGON']], 'beast'],
    [['race' => 'ALL'], 'all'],
    [['race' => 'UNKNOWN'], null],
    [[], null],
];

foreach ($cases as [$card, $expected]) {
    $actual = map_creature_type($card);
    if ($actual !== $expected) {
        throw new RuntimeException(json_encode($card) . ': expected ' . var_export($expected, true) . ', got ' . var_export($actual, true));
    }
    if ($actual !== null && !isset(creature_types()[$actual])) {
        throw new RuntimeException('Imported type missing from API dictionary: ' . $actual);
    }
}
if (creature_types()['aberration'] !== 'Аберрация') {
    throw new RuntimeException('Missing Russian aberration label');
}
echo "Battleground creature type tests passed\n";
function same($actual, $expected): void {
    if ($actual !== $expected) throw new RuntimeException(json_encode([$actual, $expected]));
}
same(battleground_creature_type(['race'=>'ABERRATION']), 'aberration');
same(battleground_creature_type(['races'=>['ABERRATION','UNDEAD']]), 'aberration');
same(battleground_creature_type(['race'=>'MECHANICAL']), 'mech');
same(battleground_creature_type(['race'=>'ALL']), 'all');
same(battleground_creature_type(['race'=>'FUTURE_UNKNOWN']), null);
same(battleground_creature_type(['text'=>'Your Aberrations gain +1/+1']), null);
same(battleground_creature_type_labels()['aberration'], 'Аберрация');
same(battleground_aberration_repair(['creature_type'=>null,'card_type'=>'minion'], ['ru'=>['race'=>'ABERRATION'], 'en'=>['race'=>'ABERRATION']]), true);
same(battleground_aberration_repair(['creature_type'=>'beast','card_type'=>'minion'], ['ru'=>['race'=>'ABERRATION']]), false);
same(battleground_aberration_repair(['creature_type'=>null,'card_type'=>'spell'], ['ru'=>['race'=>'ABERRATION']]), false);
same(battleground_aberration_repair(['creature_type'=>null,'card_type'=>'minion'], ['ru'=>['race'=>'ABERRATION'],'en'=>['race'=>'BEAST']]), false);
same(battleground_aberration_repair(['creature_type'=>null,'card_type'=>'minion'], ['ru'=>['race'=>'ABERRATION'],'en'=>['race'=>'FUTURE_UNKNOWN']]), false);
echo "OK: upstream Aberration classification and conservative repair\n";
