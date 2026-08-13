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
            'version' => 'logical-source-observed-v9',
            'unit' => 'one terminal outcome per source in a refresh run',
            'scope' => 'observed_scrape_and_pipeline_sources',
            'completeness' => 'observed_attempts_plus_recorded_run_deficits',
            'limitations' => [
                'entirely_missing_scheduled_runs_not_detectable_until_ledger',
                'best_effort_write_gaps_not_detectable',
            ],
            'coverage_method' => 'complete_generic_refresh_per_24h_bucket',
            'coverage_scope' => 'generic_scrape_sources_only',
            'coverage_cohort_method' => 'current_canonical_scrape_registry_hash',
            'combined_slo_readiness' => 'collecting_pipeline_schedule_ledger',
            'eligible_outcomes' => ['fresh_published', 'provisional', 'lkg_served', 'failed', 'timed_out'],
            'excluded_outcomes' => ['skipped'],
            'missing_terminal_method' => 'sum_positive_expected_minus_distinct_terminal_rows_per_recorded_logical_refresh',
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
                'observed_eligible_attempts' => 7,
                'missing_terminal_windows' => 2,
                'eligible_attempts' => 9,
                'counts' => [
                    'fresh_published' => 7,
                    'provisional' => 0,
                    'lkg_served' => 0,
                    'failed' => 0,
                    'timed_out' => 0,
                    'skipped' => 1,
                ],
                'full_fresh_rate_pct' => 77.78,
                'accepted_fresh_rate_pct' => 77.78,
                'data_available_rate_pct' => 77.78,
            ],
            [
                'window' => '7d',
                'from_at' => '2026-08-04T03:30:00+00:00',
                'to_at' => '2026-08-11T03:30:00+00:00',
                'measurement_status' => 'observed',
                'coverage_ratio' => 1.0,
                'total_attempts' => 105,
                'observed_eligible_attempts' => 100,
                'missing_terminal_windows' => 0,
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
assert_same('24h', $normalized['default_window'], 'The complete daily window must be the UI default.');
assert_same(2, count($normalized['windows']), 'Known windows must be retained.');
assert_same(false, $normalized['windows'][0]['rates_observed'], 'Collecting rates must never be presented as observed.');
assert_same(true, $normalized['windows'][0]['rates_available'], 'Consistent collecting rates may be presented as preliminary.');
assert_same(77.78, $normalized['windows'][0]['full_fresh_rate_pct'], 'Missing terminals must reduce the honest fresh rate.');
assert_same(7, $normalized['windows'][0]['observed_eligible_attempts'], 'Observed eligible outcomes must remain explicit.');
assert_same(2, $normalized['windows'][0]['missing_terminal_windows'], 'Missing terminal outcomes must reach the UI.');
assert_same(9, $normalized['windows'][0]['eligible_attempts'], 'Missing terminals must be included in the denominator.');
assert_same(true, $normalized['windows'][1]['rates_observed'], 'An observed window with consistent attempts may show rates.');
assert_same(true, $normalized['windows'][1]['rates_available'], 'Observed rates must also be available.');
assert_same(5, $normalized['windows'][1]['counts']['lkg_served'], 'LKG must remain a separate count.');
assert_same(
    'observed_scrape_and_pipeline_sources',
    $normalized['methodology']['scope'],
    'The UI must retain the combined observed-source scope.'
);
assert_same(
    ['entirely_missing_scheduled_runs_not_detectable_until_ledger', 'best_effort_write_gaps_not_detectable'],
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
assert_same(
    false,
    $legacyStatus['windows'][1]['rates_available'],
    'An unknown measurement status must not expose a preliminary rate either.'
);

$inconsistentEnvelope = $envelope;
$inconsistentEnvelope['data']['windows'][0]['eligible_attempts'] = 6;
$inconsistent = analytics_normalize_parsing_reliability($inconsistentEnvelope);
assert_same(
    false,
    $inconsistent['windows'][0]['rates_available'],
    'Inconsistent attempt counts must suppress preliminary rates.'
);

$missingScopeEnvelope = $envelope;
unset($missingScopeEnvelope['data']['methodology']['scope']);
$missingScope = analytics_normalize_parsing_reliability($missingScopeEnvelope);
assert_same('collecting', $missingScope['state'], 'Telemetry without the final scope contract must fail closed.');
assert_same([], $missingScope['windows'], 'Telemetry without scope must not expose rates.');

$missingLimitationEnvelope = $envelope;
$missingLimitationEnvelope['data']['methodology']['limitations'] = [
    'entirely_missing_scheduled_runs_not_detectable_until_ledger',
];
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
