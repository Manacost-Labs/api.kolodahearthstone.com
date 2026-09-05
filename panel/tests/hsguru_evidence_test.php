<?php
declare(strict_types=1);
require dirname(__DIR__) . '/lib/analytics.php';

function evidence_assert(bool $condition, string $message): void
{
    if (!$condition) throw new RuntimeException($message);
}

$fetch = ['payload' => ['sources' => [[
    'source_id' => 'hsguru_archetype_analysis', 'site' => 'hsguru',
    'state' => 'ok', 'stale' => false, 'has_dataset' => true,
    'fetched_at' => gmdate(DATE_ATOM),
    'data_evidence' => [
        'schema_version' => 1, 'has_dataset' => true,
        'collection' => ['fetched_at' => gmdate(DATE_ATOM)],
        'upstream' => ['status' => 'unknown', 'as_of' => null],
        'coverage' => ['status' => 'partial', 'scope' => 'observed_archetype_components', 'observed_archetypes' => 2],
        'components' => [['name' => 'card_stats', 'entities_total' => 2, 'state_counts' => ['cached' => 1, 'unknown' => 1], 'oldest_updated_at' => '2025-12-01T00:00:00Z', 'missing_updated_at_count' => 1]],
    ],
]]], 'cached' => false, 'stale_cache' => false, 'cache_age' => 0];
$result = analytics_normalize('overview', ['title' => 'Источники', 'description' => ''], $fetch);
$row = $result['rows'][0];
evidence_assert(!str_contains($row['age'], 'Актуально'), 'Collection cannot prove upstream freshness');
evidence_assert($row['upstream_age'] === 'Не подтверждена', 'Unknown upstream must be visible');
evidence_assert($row['coverage'] === 'Частичные компоненты', 'Partial components must be visible');
evidence_assert($row['age_tone'] !== 'good', 'Unverified HSGuru freshness must not be green');
evidence_assert($row['components'][0]['oldest_updated_at'] === '2025-12-01T00:00:00Z', 'Old cache evidence must survive');
evidence_assert($row['components'][0]['state_summary'] === 'из кэша: 1 · неизвестно: 1', 'Component counts must render as readable text, not hidden objects');
evidence_assert($row['components'][0]['name'] === 'Статистика карт', 'Component names must be readable');

$fetch['payload']['sources'][0]['data_evidence']['collection']['fetched_at'] = null;
$fetch['payload']['sources'][0]['data_evidence']['has_dataset'] = false;
$row = analytics_normalize('overview', ['title' => 'Test', 'description' => ''], $fetch)['rows'][0];
evidence_assert($row['fetched_at'] === null, 'Do not use attempt time without a dataset');

unset($fetch['payload']['sources'][0]['data_evidence']);
$row = analytics_normalize('overview', ['title' => 'Test', 'description' => ''], $fetch)['rows'][0];
evidence_assert($row['upstream_age'] === 'Не подтверждена', 'Old API responses must fail closed');
evidence_assert(!str_contains($row['age'], 'Актуально'), 'Old API response cannot invent verified freshness');
echo "HSGuru evidence tests passed\n";
