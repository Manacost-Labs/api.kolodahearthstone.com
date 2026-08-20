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
            'version' => 'logical-source-observed-v15',
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
            'combined_slo_readiness' => 'ready',
            'eligible_outcomes' => ['fresh_published', 'provisional', 'lkg_served', 'failed', 'timed_out'],
            'excluded_outcomes' => ['skipped'],
            'missing_terminal_method' => 'sum_positive_expected_minus_distinct_terminal_rows_per_recorded_logical_refresh',
        ],
        'generated_at' => '2026-08-11T03:30:00+00:00',
        'coverage_started_at' => '2026-08-01T00:00:00+00:00',
        'convergence' => [
            'ledger_status' => 'observed',
            'policy_version' => 1,
            'total_chains' => 5,
            'affected_sources' => 4,
            'chain_states' => [
                'waiting' => 1,
                'running' => 0,
                'fresh' => 2,
                'upstream_pending' => 1,
                'paused' => 0,
                'quarantined' => 0,
                'diagnosis_required' => 0,
                'exhausted' => 1,
                'cancelled' => 0,
            ],
            'total_attempts' => 3,
            'attempt_states' => [
                'queued' => 0,
                'running' => 0,
                'succeeded' => 2,
                'failed' => 1,
                'cancelled' => 0,
            ],
            'paid_requests' => 1,
            'paid_cost_usd' => '0.001500',
            'last_updated_at' => '2026-08-11T03:29:00+00:00',
            'planner' => [
                'mode' => 'shadow',
                'last_run_at' => '2026-08-11T03:29:00+00:00',
                'scanned_terminal_events' => 4,
                'scanned_missing_slots' => 1,
                'planned_chains' => 2,
                'planned_sources' => 2,
                'skipped_events' => 2,
            ],
        ],
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
                'upstream_pending_attempts' => 1,
                'end_to_end_attempts' => 10,
                'counts' => [
                    'fresh_published' => 7,
                    'provisional' => 0,
                    'lkg_served' => 0,
                    'failed' => 0,
                    'timed_out' => 0,
                    'skipped' => 1,
                ],
                'outcome_recovery' => [
                    'provisional' => [
                        'events' => 0,
                        'recovered_to_fresh' => 0,
                        'reclassified_upstream_pending' => 0,
                        'unresolved' => 0,
                    ],
                    'lkg_served' => [
                        'events' => 0,
                        'recovered_to_fresh' => 0,
                        'reclassified_upstream_pending' => 0,
                        'unresolved' => 0,
                    ],
                ],
                'full_fresh_rate_pct' => 77.78,
                'end_to_end_fresh_rate_pct' => 70.0,
                'accepted_fresh_rate_pct' => 77.78,
                'data_available_rate_pct' => 77.78,
                'freshness_slo' => [
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'collecting',
                    'good_attempts' => 7,
                    'bad_attempts' => 2,
                    'allowed_bad_attempts' => 0.09,
                    'bad_attempts_over_budget' => 2,
                    'error_budget_remaining_attempts' => -1.91,
                    'error_budget_consumed_pct' => 2222.22,
                ],
                'end_to_end_freshness_slo' => [
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'collecting',
                    'good_attempts' => 7,
                    'bad_attempts' => 3,
                    'allowed_bad_attempts' => 0.1,
                    'bad_attempts_over_budget' => 3,
                    'error_budget_remaining_attempts' => -2.9,
                    'error_budget_consumed_pct' => 3000.0,
                ],
                'verified_completeness' => [
                    'instrumented_sources' => 4,
                    'catalog_sources' => 99,
                    'source_catalog_coverage_pct' => 4.04,
                    'observed_instrumented_sources' => 2,
                    'instrumented_source_observation_coverage_pct' => 50.0,
                    'sources_meeting_target' => 2,
                    'sources_below_target' => 0,
                    'sources_without_observations' => 2,
                    'source_target_attainment_pct' => 50.0,
                    'macro_complete_fresh_rate_pct' => 50.0,
                    'macro_target_met' => false,
                    'worst_observed_source_rate_pct' => 100.0,
                    'tracked_attempts' => 2,
                    'complete_fresh' => 2,
                    'states' => [
                        'complete' => 2,
                        'incomplete' => 0,
                        'unknown' => 0,
                    ],
                    'coverage_of_all_parser_attempts_pct' => 22.22,
                    'complete_fresh_rate_pct' => 100.0,
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'collecting',
                ],
                'scheduled_reliability' => [
                    'ledger_status' => 'partial',
                    'measurement_status' => 'collecting',
                    'schedule_coverage_ratio' => 0.5,
                    'temporal_coverage_ratio' => 1.0,
                    'coverage_started_at' => '2026-08-10T03:30:00+00:00',
                    'materialized_through' => '2026-08-12T03:30:00+00:00',
                    'tracked_schedules' => 1,
                    'catalog_schedules' => 2,
                    'expected_slots' => 12,
                    'eligible_slots' => 10,
                    'excluded_slots' => 2,
                    'pending_slots' => 2,
                    'due_slots' => 8,
                    'on_time_fresh' => 7,
                    'on_time_upstream_pending' => 1,
                    'on_time_nonfresh' => 0,
                    'late' => 0,
                    'missing' => 0,
                    'on_time_fresh_rate_pct' => 87.5,
                    'parser_eligible_due_slots' => 7,
                    'parser_on_time_fresh_rate_pct' => 100.0,
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'collecting',
                    'parser_objective_status' => 'collecting',
                ],
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
                'outcome_recovery' => [
                    'provisional' => [
                        'events' => 4,
                        'recovered_to_fresh' => 3,
                        'reclassified_upstream_pending' => 0,
                        'unresolved' => 1,
                    ],
                    'lkg_served' => [
                        'events' => 5,
                        'recovered_to_fresh' => 2,
                        'reclassified_upstream_pending' => 2,
                        'unresolved' => 1,
                    ],
                ],
                'full_fresh_rate_pct' => 88.0,
                'accepted_fresh_rate_pct' => 92.0,
                'data_available_rate_pct' => 97.0,
                'freshness_slo' => [
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'breached',
                    'good_attempts' => 88,
                    'bad_attempts' => 12,
                    'allowed_bad_attempts' => 1.0,
                    'bad_attempts_over_budget' => 11,
                    'error_budget_remaining_attempts' => -11.0,
                    'error_budget_consumed_pct' => 1200.0,
                ],
                'verified_completeness' => [
                    'instrumented_sources' => 100,
                    'catalog_sources' => 100,
                    'source_catalog_coverage_pct' => 100.0,
                    'observed_instrumented_sources' => 100,
                    'instrumented_source_observation_coverage_pct' => 100.0,
                    'sources_meeting_target' => 99,
                    'sources_below_target' => 1,
                    'sources_without_observations' => 0,
                    'source_target_attainment_pct' => 99.0,
                    'macro_complete_fresh_rate_pct' => 99.0,
                    'macro_target_met' => true,
                    'worst_observed_source_rate_pct' => 0.0,
                    'tracked_attempts' => 100,
                    'complete_fresh' => 99,
                    'states' => [
                        'complete' => 99,
                        'incomplete' => 1,
                        'unknown' => 0,
                    ],
                    'coverage_of_all_parser_attempts_pct' => 100.0,
                    'complete_fresh_rate_pct' => 99.0,
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'met',
                ],
                'scheduled_reliability' => [
                    'ledger_status' => 'covered',
                    'measurement_status' => 'observed',
                    'schedule_coverage_ratio' => 1.0,
                    'temporal_coverage_ratio' => 1.0,
                    'coverage_started_at' => '2026-08-01T00:00:00+00:00',
                    'materialized_through' => '2026-08-12T03:30:00+00:00',
                    'tracked_schedules' => 2,
                    'catalog_schedules' => 2,
                    'expected_slots' => 102,
                    'eligible_slots' => 100,
                    'excluded_slots' => 2,
                    'pending_slots' => 0,
                    'due_slots' => 100,
                    'on_time_fresh' => 99,
                    'on_time_nonfresh' => 0,
                    'late' => 1,
                    'missing' => 0,
                    'on_time_fresh_rate_pct' => 99.0,
                    'target_rate_pct' => 99.0,
                    'objective_status' => 'meeting',
                ],
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
assert_same(70.0, $normalized['windows'][0]['end_to_end_fresh_rate_pct'], 'Upstream publication gaps must reduce end-to-end freshness.');
assert_same(1, $normalized['windows'][0]['upstream_pending_attempts'], 'Verified upstream gaps must remain explicit.');
assert_same(10, $normalized['windows'][0]['end_to_end_attempts'], 'End-to-end denominator must include verified upstream gaps.');
assert_same(7, $normalized['windows'][0]['observed_eligible_attempts'], 'Observed eligible outcomes must remain explicit.');
assert_same(2, $normalized['windows'][0]['missing_terminal_windows'], 'Missing terminal outcomes must reach the UI.');
assert_same(9, $normalized['windows'][0]['eligible_attempts'], 'Missing terminals must be included in the denominator.');
assert_same(
    true,
    $normalized['windows'][0]['freshness_slo']['reported'],
    'A reconciled preliminary fresh-only budget must reach the panel.'
);
assert_same(
    'collecting',
    $normalized['windows'][0]['freshness_slo']['objective_status'],
    'A partial measurement must keep the budget explicitly preliminary.'
);
assert_same(
    2,
    $normalized['windows'][0]['freshness_slo']['bad_attempts_over_budget'],
    'The panel must expose the exact number of attempts over budget.'
);
assert_same(true, $normalized['windows'][1]['rates_observed'], 'An observed window with consistent attempts may show rates.');
assert_same(true, $normalized['windows'][1]['rates_available'], 'Observed rates must also be available.');
assert_same(5, $normalized['windows'][1]['counts']['lkg_served'], 'LKG must remain a separate count.');
assert_same(
    3,
    $normalized['windows'][1]['outcome_recovery']['provisional']['recovered_to_fresh'],
    'Recovered provisional events must remain distinct from unresolved events.'
);
assert_same(
    2,
    $normalized['windows'][1]['outcome_recovery']['lkg_served']['reclassified_upstream_pending'],
    'Verified upstream publication delays must explain historical LKG events.'
);
assert_same(
    1,
    $normalized['windows'][1]['outcome_recovery']['lkg_served']['unresolved'],
    'The panel must expose the LKG events that still have no resolution.'
);
assert_same(
    'breached',
    $normalized['windows'][1]['freshness_slo']['objective_status'],
    'An observed 88% window must show a breached 99% budget.'
);
assert_same(
    -11.0,
    $normalized['windows'][1]['freshness_slo']['error_budget_remaining_attempts'],
    'A negative remaining budget must not be hidden.'
);
assert_same(
    true,
    $normalized['convergence']['reported'],
    'A coherent convergence ledger must reach the panel.'
);
assert_same(
    2,
    $normalized['convergence']['chain_states']['fresh'],
    'Fresh recovery chains must remain separate from the primary SLO.'
);
assert_same(
    '0.001500',
    $normalized['convergence']['paid_cost_usd'],
    'Confirmed recovery spend must remain visible.'
);

$invalidConvergence = $envelope;
$invalidConvergence['data']['convergence']['chain_states']['fresh'] = 3;
$invalidConvergence = analytics_normalize_parsing_reliability($invalidConvergence);
assert_same(
    false,
    $invalidConvergence['convergence']['reported'],
    'Contradictory convergence totals must fail closed.'
);

$invalidFreshnessBudget = $envelope;
$invalidFreshnessBudget['data']['windows'][1]['freshness_slo']['allowed_bad_attempts'] = 12.0;
$invalidFreshnessBudget = analytics_normalize_parsing_reliability($invalidFreshnessBudget);
assert_same(
    false,
    $invalidFreshnessBudget['windows'][1]['freshness_slo']['reported'],
    'Contradictory budget arithmetic must fail closed.'
);

$invalidOutcomeRecovery = $envelope;
$invalidOutcomeRecovery['data']['windows'][1]['outcome_recovery']['lkg_served']['unresolved'] = 2;
$invalidOutcomeRecovery = analytics_normalize_parsing_reliability($invalidOutcomeRecovery);
assert_same(
    false,
    $invalidOutcomeRecovery['windows'][1]['outcome_recovery']['reported'],
    'Contradictory recovery arithmetic must fail closed without hiding the whole window.'
);
assert_same(
    true,
    $normalized['windows'][0]['scheduled_reliability']['reported'],
    'A coherent partial schedule ledger must remain visible as preliminary evidence.'
);
assert_same(
    'collecting',
    $normalized['windows'][0]['scheduled_reliability']['measurement_status'],
    'A partial schedule ledger must remain collecting.'
);
assert_same(
    87.5,
    $normalized['windows'][0]['scheduled_reliability']['on_time_fresh_rate_pct'],
    'A coherent partial ledger may expose its explicitly preliminary on-time rate.'
);
assert_same(
    1,
    $normalized['windows'][0]['scheduled_reliability']['on_time_upstream_pending'],
    'A verified upstream gap must be a terminal schedule state instead of missing.'
);
assert_same(
    100.0,
    $normalized['windows'][0]['scheduled_reliability']['parser_on_time_fresh_rate_pct'],
    'Parser schedule reliability must exclude only verified upstream gaps.'
);
assert_same(
    true,
    $normalized['windows'][1]['scheduled_reliability']['reported'],
    'A fully covered ledger must reach the panel.'
);
assert_same(
    'observed',
    $normalized['windows'][1]['scheduled_reliability']['measurement_status'],
    'Only a fully covered ledger may be observed.'
);
assert_same(
    'meeting',
    $normalized['windows'][1]['scheduled_reliability']['objective_status'],
    'The schedule objective must reconcile with exact due and on-time counts.'
);

$validParsesUnixRollout = [
    'observed_attempts' => 3,
    'observed_sources' => 3,
    'shadow_attempts' => 1,
    'active_attempts' => 2,
    'transport_checked' => 3,
    'transport_validated' => 2,
    'transport_validated_rate_pct' => 66.67,
    'candidate_checked' => 2,
    'candidate_validated' => 2,
    'candidate_validated_rate_pct' => 100.0,
    'publication_checked' => 2,
    'publication_validated' => 1,
    'publication_validated_rate_pct' => 50.0,
    'http_status_compared' => 1,
    'http_status_matches' => 1,
    'http_status_match_rate_pct' => 100.0,
    'content_hash_compared' => 1,
    'content_hash_matches' => 0,
    'content_hash_match_rate_pct' => 0.0,
    'paid_requests_known_attempts' => 3,
    'paid_requests' => 0,
    'paid_cost_known_attempts' => 3,
    'paid_cost_usd' => '0.000000',
];
$normalizedParsesUnixRollout = analytics_normalize_parsesunix_rollout(
    $validParsesUnixRollout
);
assert_same(
    true,
    $normalizedParsesUnixRollout['reported'],
    'A coherent ParsesUnix rollout funnel must reach the panel.'
);
assert_same(
    66.67,
    $normalizedParsesUnixRollout['transport_validated_rate_pct'],
    'Transport validation must stay separate from candidate and publication validation.'
);
assert_same(
    50.0,
    $normalizedParsesUnixRollout['publication_validated_rate_pct'],
    'Only active attempts may contribute to the publication funnel.'
);
assert_same(
    '0.000000',
    $normalizedParsesUnixRollout['paid_cost_usd'],
    'A fully known exact zero cost may remain visible after observed attempts.'
);

$envelopeWithParsesUnix = $envelope;
$envelopeWithParsesUnix['data']['windows'][0]['parsesunix_rollout'] =
    $validParsesUnixRollout;
$envelopeWithParsesUnix = analytics_normalize_parsing_reliability(
    $envelopeWithParsesUnix
);
assert_same(
    true,
    $envelopeWithParsesUnix['windows'][0]['parsesunix_rollout']['reported'],
    'The normalized reliability window must expose its separate rollout block.'
);

$contradictoryParsesUnix = $validParsesUnixRollout;
$contradictoryParsesUnix['candidate_checked'] = 3;
$contradictoryParsesUnix = analytics_normalize_parsesunix_rollout(
    $contradictoryParsesUnix
);
assert_same(
    false,
    $contradictoryParsesUnix['reported'],
    'Candidate checks cannot exceed transports that passed validation.'
);
assert_same(
    null,
    $contradictoryParsesUnix['candidate_validated_rate_pct'],
    'Contradictory rollout telemetry must not leak a plausible percentage.'
);

$unknownParsesUnixCost = $validParsesUnixRollout;
$unknownParsesUnixCost['paid_cost_known_attempts'] = 2;
$unknownParsesUnixCost['paid_cost_usd'] = null;
$unknownParsesUnixCost = analytics_normalize_parsesunix_rollout(
    $unknownParsesUnixCost
);
assert_same(
    true,
    $unknownParsesUnixCost['reported'],
    'A rollout with partial cost coverage may still expose its validation funnel.'
);
assert_same(
    null,
    $unknownParsesUnixCost['paid_cost_usd'],
    'Unknown paid cost must remain null instead of becoming zero.'
);

$missingScheduleLedger = $envelope;
unset($missingScheduleLedger['data']['windows'][0]['scheduled_reliability']);
$missingScheduleLedger = analytics_normalize_parsing_reliability($missingScheduleLedger);
assert_same(
    false,
    $missingScheduleLedger['windows'][0]['scheduled_reliability']['reported'],
    'A missing schedule ledger must fail closed.'
);
assert_same(
    null,
    $missingScheduleLedger['windows'][0]['scheduled_reliability']['on_time_fresh_rate_pct'],
    'A missing ledger must never manufacture an on-time percentage.'
);

$invalidScheduleArithmetic = $envelope;
$invalidScheduleArithmetic['data']['windows'][0]['scheduled_reliability']['due_slots'] = 7;
$invalidScheduleArithmetic = analytics_normalize_parsing_reliability($invalidScheduleArithmetic);
assert_same(
    false,
    $invalidScheduleArithmetic['windows'][0]['scheduled_reliability']['reported'],
    'Contradictory expected, due, and outcome counts must fail closed.'
);

$prematureObservedSchedule = $envelope;
$prematureObservedSchedule['data']['windows'][0]['scheduled_reliability']['measurement_status'] =
    'observed';
$prematureObservedSchedule['data']['windows'][0]['scheduled_reliability']['objective_status'] =
    'breached';
$prematureObservedSchedule = analytics_normalize_parsing_reliability(
    $prematureObservedSchedule
);
assert_same(
    'collecting',
    $prematureObservedSchedule['windows'][0]['scheduled_reliability']['measurement_status'],
    'A partial ledger must never be presented as observed.'
);
assert_same(
    null,
    $prematureObservedSchedule['windows'][0]['scheduled_reliability']['on_time_fresh_rate_pct'],
    'An invalid observed claim must not leak its percentage.'
);

$invalidScheduleObjective = $envelope;
$invalidScheduleObjective['data']['windows'][1]['scheduled_reliability']['objective_status'] =
    'breached';
$invalidScheduleObjective = analytics_normalize_parsing_reliability(
    $invalidScheduleObjective
);
assert_same(
    false,
    $invalidScheduleObjective['windows'][1]['scheduled_reliability']['reported'],
    'The objective must reconcile with exact on-time and due counts.'
);

$invalidScheduleTimestamp = $envelope;
$invalidScheduleTimestamp['data']['windows'][0]['scheduled_reliability']['coverage_started_at'] =
    '2026-02-30T00:00:00+00:00';
$invalidScheduleTimestamp = analytics_normalize_parsing_reliability(
    $invalidScheduleTimestamp
);
assert_same(
    false,
    $invalidScheduleTimestamp['windows'][0]['scheduled_reliability']['reported'],
    'Invalid ISO timestamps must fail closed.'
);
assert_same(
    true,
    $normalized['windows'][0]['verified_completeness']['reported'],
    'A coherent completeness cohort must reach the UI even while coverage is collecting.'
);
assert_same(
    100.0,
    $normalized['windows'][0]['verified_completeness']['complete_fresh_rate_pct'],
    'The cohort rate must stay separate from its incomplete coverage.'
);
assert_same(
    'collecting',
    $normalized['windows'][0]['verified_completeness']['objective_status'],
    'A perfect cohort must not claim the objective before 99% tracking coverage.'
);
assert_same(
    'met',
    $normalized['windows'][1]['verified_completeness']['objective_status'],
    'A 99% lossless-fresh rate with all coverage gates met may meet the objective.'
);
assert_same(
    4.04,
    $normalized['windows'][0]['verified_completeness']['source_catalog_coverage_pct'],
    'The panel must expose the completeness rollout across the full source catalog.'
);
assert_same(
    50.0,
    $normalized['windows'][0]['verified_completeness']['instrumented_source_observation_coverage_pct'],
    'The observed instrumented cohort must remain separate from attempt coverage.'
);
assert_same(
    22.22,
    $normalized['windows'][0]['verified_completeness']['coverage_of_all_parser_attempts_pct'],
    'Attempt coverage must include missing terminal attempts in its denominator.'
);
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

$ledgerCollectingEnvelope = $envelope;
$ledgerCollectingEnvelope['data']['methodology']['combined_slo_readiness'] =
    'collecting_pipeline_schedule_ledger';
$ledgerCollecting = analytics_normalize_parsing_reliability($ledgerCollectingEnvelope);
assert_same(
    'collecting',
    $ledgerCollecting['windows'][1]['measurement_status'],
    'An incomplete schedule ledger must downgrade an observed window to collecting.'
);
assert_same(
    false,
    $ledgerCollecting['windows'][1]['rates_observed'],
    'An incomplete schedule ledger must not expose observed rates.'
);
assert_same(
    false,
    $ledgerCollecting['windows'][1]['verified_completeness']['reported'],
    'A met objective that contradicts schedule-ledger readiness must fail closed.'
);
assert_same(
    'collecting',
    $ledgerCollecting['windows'][1]['verified_completeness']['objective_status'],
    'Schedule-ledger collection must never reach the UI as a met objective.'
);

$briefStaleCache = analytics_normalize_parsing_reliability(
    $envelope,
    true,
    true,
    120
);
assert_same(
    'collecting',
    $briefStaleCache['windows'][1]['measurement_status'],
    'A stale cache must never retain an observed measurement status.'
);
assert_same(
    false,
    $briefStaleCache['windows'][1]['rates_observed'],
    'A stale cache must never confirm an observed rate.'
);
assert_same(
    'collecting',
    $briefStaleCache['windows'][1]['verified_completeness']['objective_status'],
    'A stale cache must never retain a met completeness objective.'
);
assert_same(
    'collecting',
    $briefStaleCache['windows'][1]['scheduled_reliability']['measurement_status'],
    'A stale cache must never confirm an observed schedule window.'
);
assert_same(
    'collecting',
    $briefStaleCache['windows'][1]['scheduled_reliability']['objective_status'],
    'A stale cache must never retain a meeting schedule objective.'
);

$expiredStaleCache = analytics_normalize_parsing_reliability(
    $envelope,
    true,
    true,
    301
);
assert_same(
    'collecting',
    $expiredStaleCache['state'],
    'A reliability cache older than the bounded fallback window must be discarded.'
);
assert_same(
    [],
    $expiredStaleCache['windows'],
    'An expired reliability cache must not expose historical percentages.'
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

$withoutCompleteness = $envelope;
unset($withoutCompleteness['data']['windows'][0]['verified_completeness']);
$missingCompleteness = analytics_normalize_parsing_reliability($withoutCompleteness);
assert_same(
    false,
    $missingCompleteness['windows'][0]['verified_completeness']['reported'],
    'A legacy window without completeness observations must fail closed.'
);
assert_same(
    null,
    $missingCompleteness['windows'][0]['verified_completeness']['complete_fresh_rate_pct'],
    'A missing completeness block must never become 100%.'
);
assert_same(
    'collecting',
    $missingCompleteness['windows'][0]['verified_completeness']['objective_status'],
    'A missing completeness block must remain collecting.'
);

$inconsistentCompleteness = $envelope;
$inconsistentCompleteness['data']['windows'][1]['verified_completeness']['states']['complete'] = 100;
$invalidCompleteness = analytics_normalize_parsing_reliability($inconsistentCompleteness);
assert_same(
    false,
    $invalidCompleteness['windows'][1]['verified_completeness']['reported'],
    'Contradictory completeness counts must not reach the UI.'
);

$inconsistentSourceCoverage = $envelope;
$inconsistentSourceCoverage['data']['windows'][0]['verified_completeness']['source_catalog_coverage_pct'] = 100.0;
$invalidSourceCoverage = analytics_normalize_parsing_reliability($inconsistentSourceCoverage);
assert_same(
    false,
    $invalidSourceCoverage['windows'][0]['verified_completeness']['reported'],
    'Source rollout percentages must reconcile with instrumented and catalog counts.'
);

$inconsistentSourceTarget = $envelope;
$inconsistentSourceTarget['data']['windows'][0]['verified_completeness']['source_target_attainment_pct'] = 100.0;
$invalidSourceTarget = analytics_normalize_parsing_reliability($inconsistentSourceTarget);
assert_same(
    false,
    $invalidSourceTarget['windows'][0]['verified_completeness']['reported'],
    'Per-source target attainment must reconcile with meeting and instrumented counts.'
);

$missingMacroRate = $envelope;
unset($missingMacroRate['data']['windows'][0]['verified_completeness']['macro_complete_fresh_rate_pct']);
$invalidMissingMacro = analytics_normalize_parsing_reliability($missingMacroRate);
assert_same(
    false,
    $invalidMissingMacro['windows'][0]['verified_completeness']['reported'],
    'The macro source rate is mandatory for the per-source gate.'
);

$missingMacroTargetGate = $envelope;
unset($missingMacroTargetGate['data']['windows'][0]['verified_completeness']['macro_target_met']);
$invalidMissingMacroTargetGate = analytics_normalize_parsing_reliability($missingMacroTargetGate);
assert_same(
    false,
    $invalidMissingMacroTargetGate['windows'][0]['verified_completeness']['reported'],
    'The exact macro target decision must be present as a boolean.'
);

$contradictoryMacroTargetGate = $envelope;
$contradictoryMacroTargetGate['data']['windows'][0]['verified_completeness']['macro_target_met'] = true;
$invalidMacroTargetGate = analytics_normalize_parsing_reliability($contradictoryMacroTargetGate);
assert_same(
    false,
    $invalidMacroTargetGate['windows'][0]['verified_completeness']['reported'],
    'A true exact macro gate must fail closed when the displayed macro rate is clearly below 99%.'
);

$prematureObjective = $envelope;
$prematureObjective['data']['windows'][0]['verified_completeness']['objective_status'] = 'met';
$invalidObjective = analytics_normalize_parsing_reliability($prematureObjective);
assert_same(
    false,
    $invalidObjective['windows'][0]['verified_completeness']['reported'],
    'The objective must not be met before tracking coverage reaches 99%.'
);

$partialCatalogRollout = $envelope;
$partialCatalogRollout['data']['windows'][1]['verified_completeness'] = [
    'instrumented_sources' => 4,
    'catalog_sources' => 99,
    'source_catalog_coverage_pct' => 4.04,
    'observed_instrumented_sources' => 4,
    'instrumented_source_observation_coverage_pct' => 100.0,
    'sources_meeting_target' => 4,
    'sources_below_target' => 0,
    'sources_without_observations' => 0,
    'source_target_attainment_pct' => 100.0,
    'macro_complete_fresh_rate_pct' => 100.0,
    'macro_target_met' => true,
    'worst_observed_source_rate_pct' => 100.0,
    'tracked_attempts' => 100,
    'complete_fresh' => 100,
    'states' => ['complete' => 100, 'incomplete' => 0, 'unknown' => 0],
    'coverage_of_all_parser_attempts_pct' => 100.0,
    'complete_fresh_rate_pct' => 100.0,
    'target_rate_pct' => 99.0,
    'objective_status' => 'collecting',
];
$partialCatalog = analytics_normalize_parsing_reliability($partialCatalogRollout);
assert_same(
    true,
    $partialCatalog['windows'][1]['verified_completeness']['reported'],
    'A coherent partial catalog rollout must remain visible.'
);
assert_same(
    'collecting',
    $partialCatalog['windows'][1]['verified_completeness']['objective_status'],
    '100% attempt coverage cannot meet the objective while only 4 of 99 sources are instrumented.'
);

$collectingMeasurement = $envelope;
$collectingMeasurement['data']['windows'][1]['measurement_status'] = 'collecting';
$collectingMeasurement['data']['windows'][1]['verified_completeness']['complete_fresh'] = 100;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['states'] = [
    'complete' => 100,
    'incomplete' => 0,
    'unknown' => 0,
];
$collectingMeasurement['data']['windows'][1]['verified_completeness']['sources_meeting_target'] = 100;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['sources_below_target'] = 0;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['source_target_attainment_pct'] = 100.0;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['macro_complete_fresh_rate_pct'] = 100.0;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['macro_target_met'] = true;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['worst_observed_source_rate_pct'] = 100.0;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['complete_fresh_rate_pct'] = 100.0;
$collectingMeasurement['data']['windows'][1]['verified_completeness']['objective_status'] = 'collecting';
$notObserved = analytics_normalize_parsing_reliability($collectingMeasurement);
assert_same(
    true,
    $notObserved['windows'][1]['verified_completeness']['reported'],
    'A complete cohort remains reportable while the parent window is collecting.'
);
assert_same(
    'collecting',
    $notObserved['windows'][1]['verified_completeness']['objective_status'],
    'A parent window that is not observed must keep the completeness objective collecting.'
);

$roundedBoundary = analytics_normalize_verified_completeness(
    [
        'instrumented_sources' => 1,
        'catalog_sources' => 1,
        'source_catalog_coverage_pct' => 100.0,
        'observed_instrumented_sources' => 1,
        'instrumented_source_observation_coverage_pct' => 100.0,
        'sources_meeting_target' => 0,
        'sources_below_target' => 1,
        'sources_without_observations' => 0,
        'source_target_attainment_pct' => 0.0,
        'macro_complete_fresh_rate_pct' => 99.0,
        'macro_target_met' => false,
        'worst_observed_source_rate_pct' => 99.0,
        'tracked_attempts' => 20_000,
        'complete_fresh' => 19_799,
        'states' => ['complete' => 19_799, 'incomplete' => 201, 'unknown' => 0],
        'coverage_of_all_parser_attempts_pct' => 100.0,
        'complete_fresh_rate_pct' => 99.0,
        'target_rate_pct' => 99.0,
        'objective_status' => 'miss',
    ],
    20_000,
    true,
    'observed'
);
assert_same(
    true,
    $roundedBoundary['reported'],
    'A rounded display rate at the exact SLO boundary must remain reportable.'
);
assert_same(
    'miss',
    $roundedBoundary['objective_status'],
    '19,799 of 20,000 is below 99% even though the display percentage rounds to 99.0.'
);
assert_same(
    false,
    $roundedBoundary['macro_target_met'],
    'A rounded 99.0% macro display must preserve an exact backend miss.'
);

$weightedRateHidesRareFailure = analytics_normalize_verified_completeness(
    [
        'instrumented_sources' => 2,
        'catalog_sources' => 2,
        'source_catalog_coverage_pct' => 100.0,
        'observed_instrumented_sources' => 2,
        'instrumented_source_observation_coverage_pct' => 100.0,
        'sources_meeting_target' => 1,
        'sources_below_target' => 1,
        'sources_without_observations' => 0,
        'source_target_attainment_pct' => 50.0,
        'macro_complete_fresh_rate_pct' => 50.0,
        'macro_target_met' => false,
        'worst_observed_source_rate_pct' => 0.0,
        'tracked_attempts' => 101,
        'complete_fresh' => 100,
        'states' => ['complete' => 100, 'incomplete' => 1, 'unknown' => 0],
        'coverage_of_all_parser_attempts_pct' => 100.0,
        'complete_fresh_rate_pct' => 99.01,
        'target_rate_pct' => 99.0,
        'objective_status' => 'miss',
    ],
    101,
    true,
    'observed'
);
assert_same(
    true,
    $weightedRateHidesRareFailure['reported'],
    'A weighted success rate must remain visible alongside its per-source macro gate.'
);
assert_same(
    50.0,
    $weightedRateHidesRareFailure['source_target_attainment_pct'],
    'Only one of two sources meets the 99% target.'
);
assert_same(
    'miss',
    $weightedRateHidesRareFailure['objective_status'],
    'A 99.01% weighted attempt rate must miss when one of two sources fails its target.'
);

$roundedMacroRateHidesExactFailure = analytics_normalize_verified_completeness(
    [
        'instrumented_sources' => 100,
        'catalog_sources' => 100,
        'source_catalog_coverage_pct' => 100.0,
        'observed_instrumented_sources' => 100,
        'instrumented_source_observation_coverage_pct' => 100.0,
        'sources_meeting_target' => 99,
        'sources_below_target' => 1,
        'sources_without_observations' => 0,
        'source_target_attainment_pct' => 99.0,
        'macro_complete_fresh_rate_pct' => 98.51,
        'macro_target_met' => false,
        'worst_observed_source_rate_pct' => 0.0,
        'tracked_attempts' => 19_801,
        'complete_fresh' => 19_701,
        'states' => ['complete' => 19_701, 'incomplete' => 100, 'unknown' => 0],
        'coverage_of_all_parser_attempts_pct' => 100.0,
        'complete_fresh_rate_pct' => 99.49,
        'target_rate_pct' => 99.0,
        'objective_status' => 'miss',
    ],
    19_801,
    true,
    'observed'
);
assert_same(
    true,
    $roundedMacroRateHidesExactFailure['reported'],
    'A weighted rate near 99.5% must remain reportable when its exact macro gate says miss.'
);
assert_same(
    false,
    $roundedMacroRateHidesExactFailure['macro_target_met'],
    'The panel must preserve the exact backend macro decision instead of inferring it from rounded percentages.'
);
assert_same(
    'miss',
    $roundedMacroRateHidesExactFailure['objective_status'],
    'A 99.49% weighted rate and 99% source attainment still miss when the exact macro average is below 99%.'
);

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
assert_same(
    false,
    $legacyStatus['windows'][1]['verified_completeness']['reported'],
    'An unknown parent measurement status must also suppress completeness observations.'
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
