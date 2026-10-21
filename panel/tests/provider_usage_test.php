<?php
declare(strict_types=1);

require dirname(__DIR__) . '/lib/analytics.php';

function assert_same($expected, $actual, string $message): void
{
    if ($expected !== $actual) {
        throw new RuntimeException(
            $message . '\nExpected: ' . var_export($expected, true) . '\nActual: ' . var_export($actual, true)
        );
    }
}

$registry = analytics_module_registry();
$definition = $registry['providers'];
assert_same(true, $definition['composite'], 'Provider usage is fetched by its own admin handler');
assert_same(
    ['provider' => 'all', 'days' => '30'],
    analytics_safe_query($definition, ['provider' => 'brightdata', 'days' => '365', 'source_id' => 'bad id']),
    'Unknown provider, period and source fall back to safe defaults'
);
assert_same(
    ['provider' => 'decodo', 'days' => '7', 'source_id' => 'hsguru_meta_standard_legend'],
    analytics_safe_query($definition, ['provider' => 'decodo', 'days' => '7', 'source_id' => 'hsguru_meta_standard_legend']),
    'Valid filters are forwarded'
);

$stats = [
    'total' => 3581, 'success' => 1052, 'failed' => 2529, 'success_rate' => 29.4,
    'avg_ms' => 5120, 'p95_ms' => 14000, 'bytes' => 5 * 1024 * 1024,
    'cost_units' => 1052, 'cost_usd' => 1.052, 'usd_per_1k_success' => 1.0,
];
$usage = analytics_normalize_provider_usage([
    'days' => 30,
    'from' => '2026-09-21',
    'to' => '2026-10-20',
    'generated_at' => '2026-10-20T23:00:00+00:00',
    'totals' => $stats,
    'providers' => [
        ['provider' => 'decodo', 'cost_unit' => 'request'] + $stats,
        ['provider' => 'brightdata'] + $stats,
    ],
    'daily' => [
        ['day' => '2026-10-20', 'provider' => 'decodo'] + $stats,
        ['day' => '<script>', 'provider' => 'decodo'] + $stats,
    ],
    'failure_reasons' => [
        ['provider' => 'decodo', 'reason' => 'target_403', 'count' => 2000],
        ['provider' => 'decodo', 'reason' => ['nested'], 'count' => 1],
    ],
    'pricing' => ['decodo' => ['unit' => 'request', 'usd_per_unit' => 0.001, 'basis' => 'default'], 'x' => []],
]);
assert_same(['decodo'], array_column($usage['providers'], 'provider'), 'Unknown providers are dropped');
assert_same('Decodo', $usage['providers'][0]['label'], 'Providers get display labels');
assert_same(1, count($usage['daily']), 'Malformed days are dropped');
assert_same(1, count($usage['failure_reasons']), 'Malformed reasons are dropped');
assert_same(['decodo'], array_keys($usage['pricing']), 'Pricing keeps known providers only');
assert_same(29.4, $usage['totals']['success_rate'], 'Totals keep the dashboard success rate');
assert_same('5,0 МБ', analytics_format_bytes(5 * 1024 * 1024), 'Traffic is human readable');
assert_same('$1.05', analytics_format_usd(1.052), 'Spend is shown in dollars');
assert_same('$0.0012', analytics_format_usd(0.00123), 'Small spend keeps precision');

putenv('PANEL_PARSER_CONTROL_TOKEN_PATH=' . sys_get_temp_dir() . '/missing-provider-usage-token.json');
$response = analytics_provider_usage($definition, ['provider' => 'all', 'days' => '30']);
assert_same(true, $response['ok'], 'A missing service token degrades instead of failing the page');
assert_same('Служебный токен панели не настроен.', $response['provider_usage']['error'], 'The reason is shown without secrets');
assert_same([], $response['rows'], 'No rows without data');
assert_same(true, $response['meta']['stale'], 'Unavailable ledger is marked stale');

echo "provider_usage_test.php: ok\n";
