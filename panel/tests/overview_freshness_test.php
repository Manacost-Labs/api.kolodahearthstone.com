<?php
declare(strict_types=1);

require dirname(__DIR__) . '/lib/analytics.php';

function overview_assert_same($expected, $actual, string $message): void
{
    if ($expected !== $actual) {
        throw new RuntimeException(
            $message . '\nExpected: ' . var_export($expected, true) . '\nActual: ' . var_export($actual, true)
        );
    }
}

$oldTimestamp = gmdate(DATE_ATOM, time() - 96 * 3600);
$freshTimestamp = gmdate(DATE_ATOM, time() - 300);
$fetch = [
    'payload' => [
        'total' => 4,
        'ok_count' => 3,
        'sources' => [
            [
                'source_id' => 'firestone_standard',
                'state' => 'disabled',
                'fetched_at' => null,
                'operationally_enabled' => false,
                'stale' => false,
            ],
            [
                'source_id' => 'hsreplay_battlegrounds_compositions_screenshot',
                'state' => 'ok',
                'fetched_at' => $freshTimestamp,
                'operationally_enabled' => true,
                'stale' => false,
            ],
            [
                'source_id' => 'hsreplay_archetypes',
                'state' => 'ok',
                'fetched_at' => $oldTimestamp,
                'operationally_enabled' => true,
                'stale' => false,
            ],
            [
                'source_id' => 'vicious_syndicate_radars',
                'state' => 'upstream_pending',
                'fetched_at' => $oldTimestamp,
                'operationally_enabled' => true,
                'stale' => true,
                'stale_reason' => 'live_failed_cached',
                'serving_cached_dataset' => true,
            ],
        ],
    ],
    'cached' => false,
    'stale_cache' => false,
    'cache_age' => 0,
];

$result = analytics_normalize(
    'overview',
    ['title' => 'Источники', 'description' => 'Проверка свежести'],
    $fetch
);
$summaryByLabel = [];
foreach ($result['summary'] as $item) {
    $summaryByLabel[$item['label']] = $item['value'];
}
$rowsBySource = [];
foreach ($result['rows'] as $row) {
    $rowsBySource[$row['source']] = $row;
}

overview_assert_same(1, $summaryByLabel['Устарели'], 'Only canonical backend stale sources must be counted.');
overview_assert_same('disabled', $rowsBySource['firestone_standard']['state'], 'Disabled source state must be preserved.');
overview_assert_same('neutral', $rowsBySource['hsreplay_archetypes']['age_tone'], 'A slow-cadence source inside its own threshold must not be highlighted.');
overview_assert_same('warning', $rowsBySource['vicious_syndicate_radars']['age_tone'], 'A canonical stale source must stay visible.');

echo "overview freshness tests passed\n";
