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

$envelope = [
    'data' => [
        'methodology' => [
            'version' => 'logical-source-attempts-v1',
            'unit' => 'one terminal outcome per source in a refresh run',
            'scope' => 'generic_refresh_sources',
            'completeness' => 'observed_attempts_only',
            'limitations' => [
                'dedicated_pipeline_sources_excluded',
                'best_effort_write_gaps_not_detectable',
            ],
            'eligible_outcomes' => ['fresh_published', 'provisional', 'lkg_served', 'failed', 'timed_out'],
            'excluded_outcomes' => ['skipped'],
        ],
        'generated_at' => '2026-08-11T03:30:00+00:00',
        'coverage_started_at' => '2026-08-01T00:00:00+00:00',
        'windows' => [
            [
                'window' => '24h',
                'from_at' => '2026-08-10T03:30:00+00:00',
                'to_at' => '2026-08-11T03:30:00+00:00',
                'measurement_status' => 'collecting',
                'coverage_ratio' => 0.25,
                'total_attempts' => 8,
                'eligible_attempts' => 7,
                'counts' => [
                    'fresh_published' => 7,
                    'provisional' => 0,
                    'lkg_served' => 0,
                    'failed' => 0,
                    'timed_out' => 0,
                    'skipped' => 1,
                ],
                'full_fresh_rate_pct' => 100.0,
                'accepted_fresh_rate_pct' => 100.0,
                'data_available_rate_pct' => 100.0,
            ],
            [
                'window' => '7d',
                'from_at' => '2026-08-04T03:30:00+00:00',
                'to_at' => '2026-08-11T03:30:00+00:00',
                'measurement_status' => 'observed',
                'coverage_ratio' => 1.0,
                'total_attempts' => 105,
                'eligible_attempts' => 100,
                'counts' => [
                    'fresh_published' => 88,
                    'provisional' => 4,
                    'lkg_served' => 5,
                    'failed' => 2,
                    'timed_out' => 1,
                    'skipped' => 5,
                ],
                'full_fresh_rate_pct' => 88.0,
                'accepted_fresh_rate_pct' => 92.0,
                'data_available_rate_pct' => 97.0,
            ],
        ],
    ],
    'meta' => ['source_id' => 'parser_reliability'],
];

$normalized = analytics_normalize_parsing_reliability($envelope);

assert_same('available', $normalized['state'], 'A valid reliability envelope must be available.');
assert_same('7d', $normalized['default_window'], 'The weekly window must be the UI default.');
assert_same(2, count($normalized['windows']), 'Known windows must be retained.');
assert_same(false, $normalized['windows'][0]['rates_observed'], 'Collecting rates must never be presented as observed.');
assert_same(100.0, $normalized['windows'][0]['full_fresh_rate_pct'], 'The observed value may be retained for diagnostics.');
assert_same(true, $normalized['windows'][1]['rates_observed'], 'An observed window with consistent attempts may show rates.');
assert_same(5, $normalized['windows'][1]['counts']['lkg_served'], 'LKG must remain a separate count.');
assert_same('generic_refresh_sources', $normalized['methodology']['scope'], 'The UI must retain the generic-parser scope.');
assert_same(
    ['dedicated_pipeline_sources_excluded', 'best_effort_write_gaps_not_detectable'],
    $normalized['methodology']['limitations'],
    'The public UI must retain methodology limitations.'
);

$unavailable = analytics_normalize_parsing_reliability(null);
assert_same('collecting', $unavailable['state'], 'A missing endpoint must become an honest collecting state.');
assert_same([], $unavailable['windows'], 'Missing telemetry must not manufacture a 100% window.');

$legacyEstimate = analytics_normalize_parsing_reliability([
    'success_rate_pct' => 100,
    'estimated' => true,
]);
assert_same('collecting', $legacyEstimate['state'], 'A legacy estimate is not an observed reliability report.');
assert_same([], $legacyEstimate['windows'], 'A legacy estimate must not become an observed window.');

$legacyStatusEnvelope = $envelope;
$legacyStatusEnvelope['data']['windows'][1]['measurement_status'] = 'exact';
$legacyStatus = analytics_normalize_parsing_reliability($legacyStatusEnvelope);
assert_same(
    'collecting',
    $legacyStatus['windows'][1]['measurement_status'],
    'The removed exact status must fail closed to collecting.'
);
assert_same(
    false,
    $legacyStatus['windows'][1]['rates_observed'],
    'The removed exact status must not expose observed rates.'
);

$missingScopeEnvelope = $envelope;
unset($missingScopeEnvelope['data']['methodology']['scope']);
$missingScope = analytics_normalize_parsing_reliability($missingScopeEnvelope);
assert_same('collecting', $missingScope['state'], 'Telemetry without the final scope contract must fail closed.');
assert_same([], $missingScope['windows'], 'Telemetry without scope must not expose rates.');

$missingLimitationEnvelope = $envelope;
$missingLimitationEnvelope['data']['methodology']['limitations'] = ['dedicated_pipeline_sources_excluded'];
$missingLimitation = analytics_normalize_parsing_reliability($missingLimitationEnvelope);
assert_same(
    'collecting',
    $missingLimitation['state'],
    'Telemetry that omits a declared best-effort limitation must fail closed.'
);

putenv('HS_DATA_API_INTERNAL_URL=http://127.0.0.1:1');
$overviewWithUnavailableEndpoint = analytics_attach_parsing_reliability(['ok' => true]);
assert_same(
    'collecting',
    $overviewWithUnavailableEndpoint['parsing_reliability']['state'],
    'An unavailable reliability endpoint must not fail the overview or manufacture success.'
);
assert_same(
    [],
    $overviewWithUnavailableEndpoint['parsing_reliability']['windows'],
    'An unavailable endpoint must not inherit a legacy overview estimate.'
);

echo "parsing reliability PHP tests passed\n";
