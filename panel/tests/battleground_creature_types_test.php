<?php
declare(strict_types=1);

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
