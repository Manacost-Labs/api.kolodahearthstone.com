<?php
declare(strict_types=1);

// Statistics module model (options, KPI, top fields, class_name) on saved API responses; no network.
// The analytics cache is pre-seeded under a private temp dir and the API address is unreachable.
$cacheRoot = sys_get_temp_dir() . '/analytics-model-test-' . bin2hex(random_bytes(4));
mkdir($cacheRoot, 0700, true);
putenv('TMPDIR=' . $cacheRoot);
putenv('HS_DATA_API_INTERNAL_URL=http://127.0.0.1:9');

require dirname(__DIR__) . '/lib/analytics.php';

function model_assert($condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

function model_same($expected, $actual, string $message): void
{
    if ($expected !== $actual) {
        throw new RuntimeException($message . "\nExpected: " . var_export($expected, true) . "\nActual: " . var_export($actual, true));
    }
}

function model_fixture(string $name): array
{
    $body = file_get_contents(__DIR__ . '/fixtures/analytics/' . $name . '.json');
    return json_decode((string)$body, true, 512, JSON_THROW_ON_ERROR);
}

/** Put a saved response where analytics_fetch_json looks for it (fresh), keyed by the exact URL. */
function model_seed(string $path, array $query, array $payload): void
{
    $url = 'http://127.0.0.1:9' . $path . ($query ? '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986) : '');
    file_put_contents(analytics_cache_directory() . '/' . hash('sha256', $url) . '.json', json_encode($payload));
}

function model_seed_meta(array $query, array $payload): void
{
    model_seed('/v1/hsguru/meta', $query, $payload);
}

function model_fetch(array $payload): array
{
    return ['payload' => $payload, 'cached' => false, 'stale_cache' => false, 'cache_age' => 0];
}

function model_kpi(array $response): array
{
    $byKey = [];
    foreach ($response['summary'] as $item) {
        $byKey[$item['key']] = $item;
    }
    return $byKey;
}

/** No KPI may repeat a chosen filter. */
function model_no_filter_echo(array $response, array $query, string $module): void
{
    // Text filters only: a count that happens to equal a limit or offset is not an echo.
    $filters = array_map(
        static fn($value): string => mb_strtolower((string)$value, 'UTF-8'),
        array_values(array_filter($query, static fn($value): bool => !is_int($value)))
    );
    foreach ($response['summary'] as $item) {
        model_assert(
            !in_array(mb_strtolower((string)$item['value'], 'UTF-8'), $filters, true),
            $module . ': KPI «' . $item['label'] . '» repeats a filter (' . var_export($item['value'], true) . ')'
        );
        model_assert(isset($item['key'], $item['label'], $item['value'], $item['type']), $module . ': KPI misses key/label/value/type');
        model_assert(in_array($item['type'], ['date', 'number', 'text'], true), $module . ': unknown KPI type');
    }
}

$registry = analytics_module_registry();

// 1. Finite lists are enums with exactly the values the routers accept.
model_same('enum', $registry['archetypes']['params']['rank_range']['type'], 'rank_range is an enum');
model_same('LEGEND', $registry['archetypes']['params']['rank_range']['default'], 'rank_range default stays LEGEND');
model_same('enum', $registry['archetypes']['params']['game_type']['type'], 'game_type is an enum');
model_same(['RANKED_STANDARD', 'RANKED_WILD'], $registry['archetypes']['params']['game_type']['values'], 'game_type values');
model_same('enum', $registry['meta']['params']['min_games']['type'], 'meta min_games is an enum (the API rejects other values)');
model_same('token', $registry['meta']['params']['period']['type'], 'meta period stays a token: the list is dynamic');
model_assert(in_array('bronze_plat', $registry['meta']['params']['rank']['values'], true), 'meta rank accepts bronze_plat like the API');

// 2. An unknown enum value becomes the default (no 422).
model_same(
    ['rank_range' => 'LEGEND', 'game_type' => 'RANKED_STANDARD', 'limit' => 100, 'offset' => 0],
    analytics_safe_query($registry['archetypes'], ['rank_range' => 'TOP_SECRET', 'game_type' => 'ARENA']),
    'Unknown archetype filters fall back to defaults'
);
model_same(
    ['format' => 'standard', 'rank' => 'legend', 'period' => 'past_day', 'min_games' => '100'],
    analytics_safe_query($registry['meta'], ['format' => 'x', 'rank' => 'y', 'min_games' => '7', 'period' => 'bad period!']),
    'Unknown meta filters fall back to defaults'
);

// 3. Meta end to end on the saved Standard response.
$metaPayload = model_fixture('hsguru_meta_standard');
$catalog = model_fixture('hsguru_archetypes_standard');
$metaQuery = ['format' => 'standard', 'rank' => 'legend', 'period' => 'past_day', 'min_games' => '100'];
model_seed_meta($metaQuery, $metaPayload);
model_seed('/v1/hsguru/archetypes', ['format' => 'standard', 'limit' => 500], $catalog);

$meta = analytics_module_response('meta', []);
foreach (['ok', 'module', 'title', 'description', 'summary', 'columns', 'rows', 'meta'] as $key) {
    model_assert(array_key_exists($key, $meta), 'meta keeps the old key ' . $key);
}
foreach (['total', 'updated_at', 'source_id', 'stale', 'cached', 'stale_cache', 'cache_age'] as $key) {
    model_assert(array_key_exists($key, $meta['meta']), 'meta.meta keeps ' . $key);
}
model_same(
    ['archetype', 'winrate', 'popularity', 'games', 'turns', 'duration_minutes', 'climbing_speed'],
    array_column($meta['columns'], 'key'),
    'meta columns are unchanged'
);
model_same(count($metaPayload['data']['items']), count($meta['rows']), 'meta keeps every row');
model_assert(array_key_exists('winrate', $meta['rows'][0]) && array_key_exists('popularity', $meta['rows'][0]), 'meta rows keep winrate/popularity');

$periods = $metaPayload['meta']['available_periods'];
model_same($periods, $meta['options']['period'], 'options.period comes from available_periods');
model_assert(in_array($metaPayload['meta']['current_patch_period'], $meta['options']['period'], true), 'options.period includes the current patch');
model_same($metaPayload['meta']['current_patch_period'], $meta['option_hints']['period']['current_patch'], 'hint names the current patch');
model_same($metaPayload['meta']['current_period'], $meta['option_hints']['period']['current'], 'hint names the current window');
model_same('past_day', $meta['option_hints']['period']['default'], 'hint names the default period');
model_same($registry['meta']['params']['rank']['values'], $meta['options']['rank'], 'enum options repeat their values');
model_same(['10', '25', '50', '100', '250', '500', '1000', '2500', '5000'], $meta['options']['min_games'], 'min_games options');

// KPI: data date, sample size, count of archetypes; never a filter.
$kpi = model_kpi($meta);
model_same(['data_as_of', 'games', 'rows'], array_keys($kpi), 'meta KPI keys');
model_same(['date', 'number', 'number'], array_column(array_values($kpi), 'type'), 'meta KPI types');
model_same('Данные на', $kpi['data_as_of']['label'], 'meta data label');
model_same('Игр в выборке', $kpi['games']['label'], 'meta games label');
model_same('Архетипов', $kpi['rows']['label'], 'meta rows label');
model_same(gmdate(DATE_ATOM, strtotime($metaPayload['meta']['fetched_at'])), $kpi['data_as_of']['value'], 'data_as_of is ISO 8601');
model_same((int)array_sum(array_column($metaPayload['data']['items'], 'games')), $kpi['games']['value'], 'games is the sum over rows');
model_same(count($metaPayload['data']['items']), $kpi['rows']['value'], 'rows counts archetypes');
model_no_filter_echo($meta, $metaQuery, 'meta');

// Top: numeric share/winrate, most played first.
foreach ($meta['rows'] as $row) {
    model_assert(is_int($row['share_pct']) || is_float($row['share_pct']), 'share_pct is numeric');
    model_assert(is_int($row['winrate_pct']) || is_float($row['winrate_pct']), 'winrate_pct is numeric');
}
$shares = array_column($meta['rows'], 'share_pct');
$sorted = $shares;
rsort($sorted);
model_same($sorted, $shares, 'meta rows are ordered by share, descending');
model_same($meta['rows'][0]['popularity'], $meta['rows'][0]['share_pct'], 'share_pct mirrors popularity');

// class_name: >= 90% of rows on the saved Standard response, in the catalog's own form.
$withClass = count(array_filter($meta['rows'], static fn(array $row): bool => is_string($row['class_name']) && $row['class_name'] !== ''));
model_assert($withClass / count($meta['rows']) >= 0.9, 'class_name covers at least 90% of meta rows (' . $withClass . '/' . count($meta['rows']) . ')');
$expectedClass = [];
foreach ($catalog['data'] as $archetype) {
    if (!empty($archetype['decks'][0]['class'])) {
        $expectedClass[$archetype['archetype']] = $archetype['decks'][0]['class'];
    }
}
foreach ($meta['rows'] as $row) {
    if (isset($expectedClass[$row['archetype']])) {
        model_same($expectedClass[$row['archetype']], $row['class_name'], 'class_name is the catalog value for ' . $row['archetype']);
    } else {
        model_same(null, $row['class_name'], 'no catalog match gives null for ' . $row['archetype']);
    }
}

// Unknown archetype: null class, module still fine.
$orphan = $metaPayload;
$orphan['data']['items'][] = ['archetype' => 'Totally Unknown Deck', 'winrate' => 50.0, 'popularity' => 0.1, 'games' => 100];
$orphanResult = analytics_normalize('meta', $registry['meta'], model_fetch($orphan) + ['class_catalog' => analytics_hsguru_class_catalog('standard', 120)]);
$unknown = array_values(array_filter($orphanResult['rows'], static fn(array $row): bool => $row['archetype'] === 'Totally Unknown Deck'));
model_same(null, $unknown[0]['class_name'], 'unmatched archetype has class_name null');

// A failed catalog must not break the meta module.
$brokenCacheDir = analytics_cache_directory();
foreach (glob($brokenCacheDir . '/*.json') as $file) {
    unlink($file);
}
model_seed_meta($metaQuery, $metaPayload);
$noCatalog = analytics_module_response('meta', []);
model_same(true, $noCatalog['ok'], 'meta works without the class catalog');
model_same([null], array_values(array_unique(array_column($noCatalog['rows'], 'class_name'))), 'class_name is null for all rows without the catalog');
model_same($periods, $noCatalog['options']['period'], 'options survive a catalog failure');

// 4. Unknown (well-formed) period → default, no failure; known period is kept.
$patch = $metaPayload['meta']['current_patch_period'];
model_seed_meta(array_merge($metaQuery, ['period' => $patch]), $metaPayload);
$patched = analytics_module_response('meta', ['period' => $patch]);
model_same(true, $patched['ok'], 'known patch period works');
model_assert(!isset($patched['warnings']), 'a known period gives no warning');
$unknownPeriod = analytics_module_response('meta', ['period' => 'patch_1.0.0']);
model_same(true, $unknownPeriod['ok'], 'unknown period falls back without an error');
model_assert(isset($unknownPeriod['warnings'][0]) && str_contains($unknownPeriod['warnings'][0], 'patch_1.0.0'), 'the replacement is reported in warnings');

// Fallback list when the API gives no periods; the patch is still offered.
$bare = $metaPayload;
unset($bare['meta']['available_periods']);
$bareResult = analytics_normalize('meta', $registry['meta'], model_fetch($bare));
model_same(ANALYTICS_ROLLING_PERIODS, array_slice($bareResult['options']['period'], 0, 7), 'rolling periods are the fallback');
model_assert(in_array($patch, $bareResult['options']['period'], true), 'the fallback still includes the current patch');

// 5. Archetypes (HSReplay).
$archetypesPayload = model_fixture('constructed_archetypes');
$archetypesQuery = ['rank_range' => 'LEGEND', 'game_type' => 'RANKED_STANDARD', 'limit' => 100, 'offset' => 0];
$archetypes = analytics_normalize('archetypes', $registry['archetypes'], model_fetch($archetypesPayload));
$archetypes = analytics_attach_options($registry['archetypes'], $archetypes);
model_same(['LEGEND', 'DIAMOND_FOUR_THROUGH_DIAMOND_ONE', 'DIAMOND', 'BRONZE_THROUGH_GOLD'], $archetypes['options']['rank_range'], 'archetypes rank_range options');
model_same(['data_as_of', 'games', 'rows'], array_keys(model_kpi($archetypes)), 'archetypes KPI keys');
model_no_filter_echo($archetypes, $archetypesQuery, 'archetypes');
model_same($archetypesPayload['meta']['count'], model_kpi($archetypes)['rows']['value'], 'archetypes rows is the total count');
foreach (['name', 'class_name', 'win_rate', 'pct_of_total', 'total_games'] as $key) {
    model_assert(array_key_exists($key, $archetypes['rows'][0]), 'archetypes row keeps ' . $key);
}
model_same($archetypes['rows'][0]['pct_of_total'], $archetypes['rows'][0]['share_pct'], 'archetype share_pct mirrors pct_of_total');
model_same($archetypes['rows'][0]['win_rate'], $archetypes['rows'][0]['winrate_pct'], 'archetype winrate_pct mirrors win_rate');
model_same($archetypes['rows'][0]['total_games'], $archetypes['rows'][0]['games'], 'archetype games mirrors total_games');
$archetypeShares = array_column($archetypes['rows'], 'share_pct');
$sortedShares = $archetypeShares;
rsort($sortedShares);
model_same($sortedShares, $archetypeShares, 'archetypes are ordered by share, descending');

// 6. Other statistics modules: KPI without filter echoes.
$hsguru = analytics_normalize('hsguru_archetypes', $registry['hsguru_archetypes'], model_fetch(array_merge($catalog, ['data' => array_slice($catalog['data'], 0, 20)])));
model_same(['data_as_of', 'games', 'rows', 'with_decks'], array_keys(model_kpi($hsguru)), 'hsguru_archetypes KPI keys');
model_no_filter_echo($hsguru, ['standard', 50, 'games', 'desc', 300, 0], 'hsguru_archetypes');
model_assert(is_float($hsguru['rows'][0]['share_pct']) || is_int($hsguru['rows'][0]['share_pct']), 'hsguru_archetypes has share_pct');

$decks = analytics_normalize('decks', $registry['decks'], model_fetch(model_fixture('constructed_decks')));
model_same(['data_as_of', 'rows'], array_keys(model_kpi($decks)), 'decks KPI keys (decks have no shared sample)');
model_no_filter_echo($decks, ['Standard', 'Wild', 50, 0], 'decks');

$arena = analytics_normalize('arena', $registry['arena'], model_fetch(model_fixture('arena_classes')));
model_same('Классов', model_kpi($arena)['rows']['label'], 'arena counts classes');
model_no_filter_echo($arena, [20, 0], 'arena');

$duos = analytics_normalize('bg_heroes', $registry['bg_heroes'], model_fetch(model_fixture('bg_heroes_duos')));
model_same('Героев', model_kpi($duos)['rows']['label'], 'bg_heroes counts heroes');
model_assert(!isset(model_kpi($duos)['games']), 'no games KPI when heroes carry no games');
model_no_filter_echo($duos, ['duos', 'solo', 50, 200, 0], 'bg_heroes');
$duoShares = array_column($duos['rows'], 'share_pct');
$sortedDuo = $duoShares;
rsort($sortedDuo);
model_same($sortedDuo, $duoShares, 'heroes are ordered by pick rate, descending');
model_same(null, $duos['rows'][0]['winrate_pct'], 'Battlegrounds heroes have no win rate');
model_assert(is_float($duos['rows'][0]['avg_placement_value']), 'avg_placement_value is numeric');

$soloRows = analytics_bg_hero_top_rows([
    ['hero' => 'A', 'pick_rate_value' => 10.0, 'avg_placement' => '4.1', 'first_place' => 12.5, 'games' => 300],
    ['hero' => 'B', 'pick_rate_value' => 40.5, 'avg_placement' => 3.9, 'first_place' => 15.0, 'games' => 900],
]);
model_same(['B', 'A'], array_column($soloRows, 'hero'), 'solo heroes: most picked first');
model_same(4.1, $soloRows[1]['avg_placement_value'], 'string placement becomes a number');
model_same(900, $soloRows[0]['games'], 'solo heroes keep games');

// Modules fed from cached datasets (bg_minions, arena_cards).
model_seed('/datasets/hsreplay_battlegrounds_minions', [], [
    'fetched_at' => '2026-11-19T10:00:00+00:00',
    'data' => ['structured' => ['minions' => [
        ['id' => 'BG_A', 'name' => 'Alpha', 'tavern_tier' => 2, 'popularity' => 5.5, 'games_with_minion' => 10],
        ['id' => 'BG_B', 'name' => 'Beta', 'tavern_tier' => 3, 'popularity' => 3.0, 'games_with_minion' => 20],
    ]]],
]);
$minions = analytics_module_response('bg_minions', ['tavern_tier' => '2']);
model_same('Существ', model_kpi($minions)['rows']['label'], 'bg_minions counts minions');
model_same(1, model_kpi($minions)['rows']['value'], 'bg_minions count follows the tier filter');
model_same(false, isset(model_kpi($minions)['games']), 'minion games overlap, so no sample KPI');
model_no_filter_echo($minions, [2, 500, 0], 'bg_minions');
model_same([], array_diff(['rows', 'data_as_of'], array_keys(model_kpi($minions))), 'bg_minions has date and count');

model_seed('/datasets/firestone_arena_cards_normal', [], [
    'fetched_at' => '2026-11-19T09:00:00+00:00',
    'data' => ['structured' => ['cards' => [['card_id' => 'X1', 'name' => 'One'], ['card_id' => 'X2', 'name' => 'Two']]]],
]);
$arenaCards = analytics_module_response('arena_cards', []);
model_same('Карт', model_kpi($arenaCards)['rows']['label'], 'arena_cards counts cards');
model_no_filter_echo($arenaCards, ['firestone', 200], 'arena_cards');
model_same(['firestone', 'hsreplay', 'underground'], (static function (array $values): array { sort($values); return $values; })($arenaCards['options']['arena_source']), 'arena_source options are the enum values');

// The summary builder in isolation (constructed_cards reads a server-local dataset).
$built = analytics_statistics_summary(model_fetch(['meta' => ['fetched_at' => '2026-11-19T10:00:00Z']]), [['x' => 1]], 'Карт', null, 500);
model_same(['data_as_of', 'rows'], array_column($built, 'key'), 'summary builder skips games without a games field');
model_same(500, $built[1]['value'], 'summary builder uses the given total');
model_same('2026-11-19T10:00:00+00:00', $built[0]['value'], 'summary builder normalises the date');
$noDate = analytics_statistics_summary(model_fetch([]), [], 'Карт');
model_same(['rows'], array_column($noDate, 'key'), 'no date, no KPI');

// Non-statistical modules keep their summary untouched and get no top fields.
$overview = analytics_normalize('overview', $registry['overview'], model_fetch(['total' => 1, 'ok_count' => 1, 'sources' => []]));
model_same(['Источников', 'Работают', 'Проблемных', 'Устарели'], array_column($overview['summary'], 'label'), 'overview summary is unchanged');
model_assert(!isset($overview['summary'][0]['key']), 'overview KPI are not touched');

// options exist only where there is something to choose from.
model_assert(!isset(analytics_attach_options($registry['arena'], ['ok' => true])['options']), 'a module without enum params has no options');

foreach (glob($cacheRoot . '/*/*') ?: [] as $file) {
    @unlink($file);
}
foreach (glob($cacheRoot . '/*', GLOB_ONLYDIR) ?: [] as $dir) {
    @rmdir($dir);
}
@rmdir($cacheRoot);

echo "analytics_model_test.php: ok\n";
