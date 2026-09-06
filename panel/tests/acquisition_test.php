<?php
declare(strict_types=1);
require dirname(__DIR__) . '/lib/analytics.php';
require_once dirname(__DIR__) . '/lib/acquisition.php';

function acquisition_assert($expected, $actual): void {
    if ($expected !== $actual) throw new RuntimeException('Acquisition assertion failed: ' . json_encode([$expected, $actual]));
}
function acquisition_fixture_observation(): array {
    return ['source_id' => 'bg', 'site' => 'hsreplay', 'kind' => 'scrape', 'description' => 'BG',
        'observed_at' => gmdate('Y-m-d\TH:i:s\Z', time() - 60), 'credits_spent' => 5, 'unknown_cost_attempts' => 1,
        'coverage' => ['source_id' => 'bg', 'scope_id' => str_repeat('a', 64), 'snapshot_id' => 'capture-1', 'patch_id' => 'patch-1',
            'query' => [], 'fragment' => '', 'status' => 'partial', 'found' => 2, 'expected_count' => 2,
            'listing_percent' => 100, 'listing_complete' => true, 'view_confirmed' => true, 'unresolved_details' => 1,
            'details' => ['succeeded' => 1, 'absent' => 0, 'failed' => 0, 'retry' => 1, 'running' => 0, 'pending' => 0, 'not_requested' => 0]]];
}
$catalog = [['source_id' => 'bg', 'site' => 'hsreplay', 'description' => 'BG'], ['source_id' => 'meta', 'site' => 'hsguru', 'description' => 'Meta']];
$fetch = ['payload' => ['sources' => $catalog], 'cached' => false, 'stale_cache' => false, 'cache_age' => 0];
$definition = ['title' => 'Покрытие', 'description' => 'Снимки'];
$empty = analytics_acquisition_normalize($definition, $fetch, ['state' => 'not_configured', 'payload' => null], []);
acquisition_assert(2, count($empty['rows']));
acquisition_assert(null, $empty['rows'][0]['found']);
acquisition_assert('unknown', $empty['rows'][0]['state_code']);
acquisition_assert(null, $empty['rows'][0]['credits_spent']);
$observation = acquisition_fixture_observation();
$artifact = ['state' => 'available', 'payload' => ['schema_version' => 1, 'generated_at' => gmdate('Y-m-d\TH:i:s\Z'), 'sources' => [$observation]]];
$result = analytics_acquisition_normalize($definition, $fetch, $artifact, []);
acquisition_assert('partial', $result['rows'][0]['state_code']);
acquisition_assert(1, $result['rows'][0]['unresolved_details']);
acquisition_assert(5, $result['rows'][0]['credits_spent']);
acquisition_assert('unknown', $result['rows'][1]['state_code']);
$filtered = analytics_acquisition_normalize($definition, $fetch, $artifact, ['q' => 'HSReplay', 'coverage_state' => 'partial']);
acquisition_assert(1, count($filtered['rows']));
acquisition_assert(0, count(analytics_acquisition_normalize($definition, $fetch, $artifact, ['q' => 'not-found'])['rows']));
foreach ([['status', 'complete_for_view'], ['found', true], ['unresolved_details', 0], ['expected_count', 1], ['listing_percent', 5], ['scope_id', 'foreign']] as [$key, $value]) {
    $broken = $artifact;
    $broken['payload']['sources'][0]['coverage'][$key] = $value;
    acquisition_assert('unknown', analytics_acquisition_normalize($definition, $fetch, $broken, [])['rows'][0]['state_code']);
}
$duplicate = $artifact;
$duplicate['payload']['sources'][] = $observation;
acquisition_assert('unknown', analytics_acquisition_normalize($definition, $fetch, $duplicate, [])['rows'][0]['state_code']);
$old = $artifact;
$old['payload']['sources'][0]['observed_at'] = '2020-01-01T00:00:00Z';
acquisition_assert('stale', analytics_acquisition_normalize($definition, $fetch, $old, [])['rows'][0]['state_code']);
putenv('HS_ACQUISITION_PANEL_REPORT');
acquisition_assert('not_configured', analytics_acquisition_read()['state']);
putenv('HS_ACQUISITION_PANEL_REPORT=https://example.invalid/private');
acquisition_assert('unavailable', analytics_acquisition_read()['state']);
putenv('HS_ACQUISITION_PANEL_REPORT');
echo "acquisition panel tests passed\n";
