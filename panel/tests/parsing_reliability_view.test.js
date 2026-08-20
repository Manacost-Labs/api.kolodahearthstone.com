'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {
    buildReliabilityViewModel,
} = require('../assets/parsing-reliability.js');

const observedWindow = {
    window: '7d',
    measurement_status: 'observed',
    rates_observed: true,
    rates_available: true,
    coverage_ratio: 1,
    observed_eligible_attempts: 98,
    missing_terminal_windows: 2,
    eligible_attempts: 100,
    upstream_pending_attempts: 2,
    end_to_end_attempts: 102,
    total_attempts: 105,
    counts: {
        fresh_published: 88,
        provisional: 4,
        lkg_served: 5,
        failed: 2,
        timed_out: 1,
        skipped: 5,
    },
    outcome_recovery: {
        reported: true,
        provisional: {
            events: 4,
            recovered_to_fresh: 3,
            reclassified_upstream_pending: 0,
            unresolved: 1,
        },
        lkg_served: {
            events: 5,
            recovered_to_fresh: 2,
            reclassified_upstream_pending: 2,
            unresolved: 1,
        },
    },
    full_fresh_rate_pct: 88,
    end_to_end_fresh_rate_pct: 86.27,
    accepted_fresh_rate_pct: 92,
    data_available_rate_pct: 97,
    freshness_slo: {
        reported: true,
        target_rate_pct: 99,
        objective_status: 'breached',
        good_attempts: 88,
        bad_attempts: 12,
        allowed_bad_attempts: 1,
        bad_attempts_over_budget: 11,
        error_budget_remaining_attempts: -11,
        error_budget_consumed_pct: 1200,
    },
    verified_completeness: {
        instrumented_sources: 4,
        catalog_sources: 4,
        source_catalog_coverage_pct: 100,
        observed_instrumented_sources: 4,
        instrumented_source_observation_coverage_pct: 100,
        sources_meeting_target: 3,
        sources_below_target: 1,
        sources_without_observations: 0,
        source_target_attainment_pct: 75,
        macro_complete_fresh_rate_pct: 98,
        macro_target_met: false,
        worst_observed_source_rate_pct: 92,
        reported: true,
        tracked_attempts: 100,
        complete_fresh: 98,
        states: {
            complete: 98,
            incomplete: 2,
            unknown: 0,
        },
        coverage_of_all_parser_attempts_pct: 100,
        complete_fresh_rate_pct: 98,
        target_rate_pct: 99,
        objective_status: 'miss',
    },
};

const coveredSchedule = {
    reported: true,
    ledger_status: 'covered',
    measurement_status: 'observed',
    schedule_coverage_ratio: 1,
    temporal_coverage_ratio: 1,
    coverage_started_at: '2026-08-01T00:00:00+00:00',
    materialized_through: '2026-08-12T00:00:00+00:00',
    tracked_schedules: 2,
    catalog_schedules: 2,
    expected_slots: 102,
    eligible_slots: 100,
    excluded_slots: 2,
    pending_slots: 0,
    due_slots: 100,
    on_time_fresh: 98,
    on_time_upstream_pending: 0,
    on_time_nonfresh: 1,
    late: 1,
    missing: 0,
    on_time_fresh_rate_pct: 98,
    parser_eligible_due_slots: 100,
    parser_on_time_fresh_rate_pct: 98,
    target_rate_pct: 99,
    objective_status: 'breached',
    parser_objective_status: 'breached',
};

const parsesUnixRollout = {
    reported: true,
    observed_attempts: 3,
    observed_sources: 3,
    shadow_attempts: 1,
    active_attempts: 2,
    transport_checked: 3,
    transport_validated: 2,
    transport_validated_rate_pct: 66.67,
    candidate_checked: 2,
    candidate_validated: 2,
    candidate_validated_rate_pct: 100,
    publication_checked: 2,
    publication_validated: 1,
    publication_validated_rate_pct: 50,
    http_status_compared: 1,
    http_status_matches: 1,
    http_status_match_rate_pct: 100,
    content_hash_compared: 1,
    content_hash_matches: 0,
    content_hash_match_rate_pct: 0,
    paid_requests_known_attempts: 3,
    paid_requests: 0,
    paid_cost_known_attempts: 3,
    paid_cost_usd: '0.000000',
};

test('describes extraction evidence without claiming full upstream pages', () => {
    const renderer = fs.readFileSync(
        path.join(__dirname, '../assets/analytics.js'),
        'utf8'
    );

    assert.match(renderer, /Проверенная полнота извлечения/);
    assert.match(renderer, /Свежие ответы без потерь извлечения/);
    assert.match(renderer, /Полнота каталога upstream/);
    assert.match(renderer, /Свежесть для пользователя/);
    assert.match(renderer, /Надёжность парсера/);
    assert.match(renderer, /Ждём upstream/);
    assert.match(renderer, /Fresh после повтора/);
    assert.match(renderer, /переклассифицированы upstream/);
    assert.match(renderer, /не закрыто/);
    assert.match(renderer, /On-time parser/);
    assert.match(renderer, /Rollout источников/);
    assert.match(renderer, /weighted по попыткам/);
    assert.match(renderer, /Источники, выполняющие 99%/);
    assert.match(renderer, /Macro rate по источникам/);
    assert.match(renderer, /Худший наблюдавшийся источник/);
    assert.match(renderer, /Выполнение расписания/);
    assert.match(renderer, /Бюджет ошибок парсера fresh-only/);
    assert.match(renderer, /Использовано бюджета/);
    assert.match(renderer, /Сверх бюджета/);
    assert.match(renderer, /On-time end-to-end/);
    assert.match(renderer, /Покрытие расписаний/);
    assert.match(renderer, /Внедрение ParsesUnix/);
    assert.match(renderer, /Транспорт подтверждён/);
    assert.match(renderer, /Кандидат пригоден/);
    assert.match(renderer, /Публикация подтверждена/);
    assert.match(renderer, /Shadow-попытки только сравниваются/);
    assert.match(renderer, /без нулевой оценки неизвестной стоимости/);
    assert.match(renderer, /предварительно/i);
    assert.doesNotMatch(renderer, /Полное получение данных|страница получена целиком/);
});

test('shows the exact honest fresh-only error budget', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [observedWindow],
    });

    assert.equal(model.freshnessSlo.reported, true);
    assert.equal(model.freshnessSlo.objectiveStatus, 'breached');
    assert.equal(model.freshnessSlo.objectiveLabel, 'Бюджет превышен');
    assert.equal(model.freshnessSlo.goodAttempts, 88);
    assert.equal(model.freshnessSlo.badAttempts, 12);
    assert.equal(model.freshnessSlo.allowedBadAttempts, 1);
    assert.equal(model.freshnessSlo.badAttemptsOverBudget, 11);
    assert.equal(model.freshnessSlo.remainingAttempts, -11);
    assert.equal(model.freshnessSlo.consumedRate, '1 200%');
});

test('fails the fresh-only budget closed on contradictory arithmetic', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            freshness_slo: {
                ...observedWindow.freshness_slo,
                bad_attempts_over_budget: 1,
            },
        }],
    });

    assert.equal(model.freshnessSlo.reported, false);
    assert.equal(model.freshnessSlo.objectiveStatus, 'collecting');
    assert.equal(model.freshnessSlo.consumedRate, '—');
});

test('shows the ParsesUnix rollout as a separate validated funnel', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{...observedWindow, parsesunix_rollout: parsesUnixRollout}],
    });

    assert.equal(model.parsesUnixRollout.reported, true);
    assert.equal(model.parsesUnixRollout.hasObservations, true);
    assert.equal(model.parsesUnixRollout.observedAttempts, 3);
    assert.equal(model.parsesUnixRollout.observedSources, 3);
    assert.equal(model.parsesUnixRollout.shadowAttempts, 1);
    assert.equal(model.parsesUnixRollout.activeAttempts, 2);
    assert.equal(model.parsesUnixRollout.transportValidatedRate, '66,67%');
    assert.equal(model.parsesUnixRollout.candidateValidatedRate, '100%');
    assert.equal(model.parsesUnixRollout.publicationValidatedRate, '50%');
    assert.equal(model.parsesUnixRollout.httpStatusMatchRate, '100%');
    assert.equal(model.parsesUnixRollout.contentHashMatchRate, '0%');
    assert.equal(model.parsesUnixRollout.paidRequests, 0);
    assert.equal(model.parsesUnixRollout.paidCostUsd, '0.000000');
});

test('fails the ParsesUnix rollout closed on contradictory counts or rates', () => {
    const invalidBlocks = [
        {...parsesUnixRollout, candidate_checked: 3},
        {...parsesUnixRollout, transport_validated_rate_pct: 100},
        {...parsesUnixRollout, shadow_attempts: 2},
        {...parsesUnixRollout, paid_cost_usd: '0'},
    ];

    invalidBlocks.forEach((parsesunix_rollout) => {
        const model = buildReliabilityViewModel({
            state: 'available',
            default_window: '7d',
            windows: [{...observedWindow, parsesunix_rollout}],
        });
        assert.equal(model.parsesUnixRollout.reported, false);
        assert.equal(model.parsesUnixRollout.hasObservations, false);
        assert.equal(model.parsesUnixRollout.transportValidatedRate, '—');
        assert.equal(model.parsesUnixRollout.paidCostUsd, null);
    });
});

test('keeps an unknown ParsesUnix cost unknown instead of showing zero', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            parsesunix_rollout: {
                reported: true,
                observed_attempts: 1,
                observed_sources: 1,
                shadow_attempts: 0,
                active_attempts: 1,
                transport_checked: 1,
                transport_validated: 0,
                transport_validated_rate_pct: 0,
                candidate_checked: 0,
                candidate_validated: 0,
                candidate_validated_rate_pct: null,
                publication_checked: 1,
                publication_validated: 0,
                publication_validated_rate_pct: 0,
                http_status_compared: 0,
                http_status_matches: 0,
                http_status_match_rate_pct: null,
                content_hash_compared: 0,
                content_hash_matches: 0,
                content_hash_match_rate_pct: null,
                paid_requests_known_attempts: 1,
                paid_requests: 1,
                paid_cost_known_attempts: 0,
                paid_cost_usd: null,
            },
        }],
    });

    assert.equal(model.parsesUnixRollout.reported, true);
    assert.equal(model.parsesUnixRollout.paidRequests, 1);
    assert.equal(model.parsesUnixRollout.paidCostKnownAttempts, 0);
    assert.equal(model.parsesUnixRollout.paidCostUsd, null);
});

test('does not display zero cost as evidence before the first rollout attempt', () => {
    const emptyRollout = Object.fromEntries(
        Object.keys(parsesUnixRollout).map((key) => {
            if (key === 'reported') return [key, true];
            if (key.endsWith('_rate_pct')) return [key, null];
            if (key === 'paid_cost_usd') return [key, '0.000000'];
            return [key, 0];
        })
    );
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{...observedWindow, parsesunix_rollout: emptyRollout}],
    });

    assert.equal(model.parsesUnixRollout.reported, true);
    assert.equal(model.parsesUnixRollout.hasObservations, false);
    assert.equal(model.parsesUnixRollout.paidRequests, null);
    assert.equal(model.parsesUnixRollout.paidCostUsd, null);
});

test('shows an observed schedule objective only for a fully covered ledger', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{...observedWindow, scheduled_reliability: coveredSchedule}],
    });

    assert.equal(model.scheduledReliability.reported, true);
    assert.equal(model.scheduledReliability.observed, true);
    assert.equal(model.scheduledReliability.preliminary, false);
    assert.equal(model.scheduledReliability.onTimeFreshRate, '98%');
    assert.equal(model.scheduledReliability.scheduleCoverage, '100%');
    assert.equal(model.scheduledReliability.temporalCoverage, '100%');
    assert.equal(model.scheduledReliability.dueSlots, 100);
    assert.equal(model.scheduledReliability.late, 1);
    assert.equal(model.scheduledReliability.objectiveStatus, 'breached');
    assert.equal(model.scheduledReliability.objectiveClass, 'is-miss');
});

test('separates end-to-end schedule delay from parser reliability', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            scheduled_reliability: {
                ...coveredSchedule,
                on_time_upstream_pending: 2,
                on_time_nonfresh: 0,
                late: 0,
                parser_eligible_due_slots: 98,
                parser_on_time_fresh_rate_pct: 100,
                parser_objective_status: 'meeting',
            },
        }],
    });

    assert.equal(model.endToEndFresh, '86,27%');
    assert.equal(model.upstreamPendingAttempts, 2);
    assert.equal(model.endToEndAttempts, 102);
    assert.equal(model.scheduledReliability.onTimeFreshRate, '98%');
    assert.equal(model.scheduledReliability.parserOnTimeFreshRate, '100%');
    assert.equal(model.scheduledReliability.onTimeUpstreamPending, 2);
    assert.equal(model.scheduledReliability.objectiveStatus, 'breached');
    assert.equal(model.scheduledReliability.parserObjectiveStatus, 'meeting');
});

test('labels a coherent partial schedule slice as explicitly preliminary', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            scheduled_reliability: {
                ...coveredSchedule,
                ledger_status: 'partial',
                measurement_status: 'collecting',
                schedule_coverage_ratio: 0.5,
                tracked_schedules: 1,
                objective_status: 'collecting',
                parser_objective_status: 'collecting',
            },
        }],
    });

    assert.equal(model.scheduledReliability.reported, true);
    assert.equal(model.scheduledReliability.observed, false);
    assert.equal(model.scheduledReliability.preliminary, true);
    assert.equal(model.scheduledReliability.onTimeFreshRate, '98%');
    assert.equal(model.scheduledReliability.scheduleCoverage, '50%');
    assert.equal(model.scheduledReliability.excludedSlots, 2);
    assert.equal(model.scheduledReliability.pendingSlots, 0);
    assert.equal(model.scheduledReliability.objectiveStatus, 'collecting');
    assert.equal(model.scheduledReliability.objectiveClass, 'is-collecting');
});

test('fails schedule telemetry closed on arithmetic, coverage, or objective contradictions', () => {
    const invalidBlocks = [
        {...coveredSchedule, due_slots: 99},
        {
            ...coveredSchedule,
            ledger_status: 'partial',
            schedule_coverage_ratio: 0.5,
            tracked_schedules: 1,
        },
        {...coveredSchedule, objective_status: 'meeting'},
        {...coveredSchedule, coverage_started_at: '2026-02-30T00:00:00+00:00'},
        {...coveredSchedule, temporal_coverage_ratio: true},
    ];

    invalidBlocks.forEach((scheduled_reliability) => {
        const model = buildReliabilityViewModel({
            state: 'available',
            default_window: '7d',
            windows: [{...observedWindow, scheduled_reliability}],
        });
        assert.equal(model.scheduledReliability.reported, false);
        assert.equal(model.scheduledReliability.measurementStatus, 'collecting');
        assert.equal(model.scheduledReliability.onTimeFreshRate, '—');
        assert.equal(model.scheduledReliability.objectiveStatus, 'collecting');
    });
});

test('does not invent a schedule percentage when the block is absent', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [observedWindow],
    });

    assert.equal(model.scheduledReliability.reported, false);
    assert.equal(model.scheduledReliability.onTimeFreshRate, '—');
    assert.equal(model.scheduledReliability.scheduleCoverage, '—');
    assert.equal(model.scheduledReliability.objectiveStatus, 'collecting');
    assert.doesNotMatch(JSON.stringify(model.scheduledReliability), /100%/);
});

test('downgrades a cached schedule objective to preliminary collecting', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        stale_cache: true,
        default_window: '7d',
        windows: [{
            ...observedWindow,
            scheduled_reliability: {
                ...coveredSchedule,
                on_time_fresh: 99,
                on_time_nonfresh: 0,
                on_time_fresh_rate_pct: 99,
                objective_status: 'meeting',
                parser_on_time_fresh_rate_pct: 99,
                parser_objective_status: 'meeting',
            },
        }],
    });

    assert.equal(model.scheduledReliability.reported, true);
    assert.equal(model.scheduledReliability.observed, false);
    assert.equal(model.scheduledReliability.preliminary, true);
    assert.equal(model.scheduledReliability.onTimeFreshRate, '99%');
    assert.equal(model.scheduledReliability.objectiveStatus, 'collecting');
});

test('uses the observed weekly window for honest headline rates', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [observedWindow],
    });

    assert.equal(model.badge, 'Наблюдаемый срез');
    assert.equal(model.fullFresh, '88%');
    assert.equal(model.availability, '97%');
    assert.equal(model.acceptedFresh, '92%');
    assert.equal(model.counts.provisional, 4);
    assert.equal(model.counts.lkg, 5);
    assert.equal(model.counts.failed, 2);
    assert.equal(model.counts.timedOut, 1);
    assert.equal(model.outcomeRecovery.reported, true);
    assert.deepEqual(model.outcomeRecovery.provisional, {
        events: 4,
        recoveredToFresh: 3,
        reclassifiedUpstreamPending: 0,
        unresolved: 1,
    });
    assert.deepEqual(model.outcomeRecovery.lkg, {
        events: 5,
        recoveredToFresh: 2,
        reclassifiedUpstreamPending: 2,
        unresolved: 1,
    });
    assert.equal(model.observedEligibleAttempts, 98);
    assert.equal(model.missingTerminalWindows, 2);
    assert.equal(model.verifiedCompleteness.completeFreshRate, '98%');
    assert.equal(model.verifiedCompleteness.attemptCoverage, '100%');
    assert.equal(model.verifiedCompleteness.sourceCatalogCoverage, '100%');
    assert.equal(model.verifiedCompleteness.instrumentedObservationCoverage, '100%');
    assert.equal(model.verifiedCompleteness.targetRate, '99%');
    assert.equal(model.verifiedCompleteness.instrumentedSources, 4);
    assert.equal(model.verifiedCompleteness.catalogSources, 4);
    assert.equal(model.verifiedCompleteness.observedInstrumentedSources, 4);
    assert.equal(model.verifiedCompleteness.sourcesMeetingTarget, 3);
    assert.equal(model.verifiedCompleteness.sourcesBelowTarget, 1);
    assert.equal(model.verifiedCompleteness.sourcesWithoutObservations, 0);
    assert.equal(model.verifiedCompleteness.sourceTargetAttainment, '75%');
    assert.equal(model.verifiedCompleteness.macroCompleteFreshRate, '98%');
    assert.equal(model.verifiedCompleteness.worstObservedSourceRate, '92%');
    assert.equal(model.verifiedCompleteness.trackedAttempts, 100);
    assert.equal(model.verifiedCompleteness.completeFresh, 98);
    assert.deepEqual(
        model.verifiedCompleteness.states,
        {complete: 98, incomplete: 2, unknown: 0}
    );
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'miss');
    assert.match(model.verifiedCompleteness.objectiveLabel, /miss/);
});

test('fails contradictory recovery arithmetic closed', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            outcome_recovery: {
                ...observedWindow.outcome_recovery,
                lkg_served: {
                    ...observedWindow.outcome_recovery.lkg_served,
                    unresolved: 2,
                },
            },
        }],
    });

    assert.equal(model.outcomeRecovery.reported, false);
    assert.equal(model.outcomeRecovery.lkg.unresolved, null);
});

test('keeps 100% attempt coverage collecting while only 4 of 99 sources are instrumented', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '24h',
        windows: [{
            ...observedWindow,
            window: '24h',
            verified_completeness: {
                reported: true,
                instrumented_sources: 4,
                catalog_sources: 99,
                source_catalog_coverage_pct: 4.04,
                observed_instrumented_sources: 4,
                instrumented_source_observation_coverage_pct: 100,
                sources_meeting_target: 4,
                sources_below_target: 0,
                sources_without_observations: 0,
                source_target_attainment_pct: 100,
                macro_complete_fresh_rate_pct: 100,
                macro_target_met: true,
                worst_observed_source_rate_pct: 100,
                tracked_attempts: 100,
                complete_fresh: 100,
                states: {complete: 100, incomplete: 0, unknown: 0},
                coverage_of_all_parser_attempts_pct: 100,
                complete_fresh_rate_pct: 100,
                target_rate_pct: 99,
                objective_status: 'collecting',
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.completeFreshRate, '100%');
    assert.equal(model.verifiedCompleteness.sourceCatalogCoverage, '4,04%');
    assert.equal(model.verifiedCompleteness.instrumentedSources, 4);
    assert.equal(model.verifiedCompleteness.catalogSources, 99);
    assert.equal(model.verifiedCompleteness.instrumentedObservationCoverage, '100%');
    assert.equal(model.verifiedCompleteness.attemptCoverage, '100%');
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
    assert.notEqual(model.verifiedCompleteness.objectiveStatus, 'met');
});

test('keeps the objective collecting while the parent measurement is not observed', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '24h',
        windows: [{
            ...observedWindow,
            window: '24h',
            measurement_status: 'collecting',
            rates_observed: false,
            verified_completeness: {
                ...observedWindow.verified_completeness,
                sources_meeting_target: 4,
                sources_below_target: 0,
                source_target_attainment_pct: 100,
                macro_complete_fresh_rate_pct: 100,
                macro_target_met: true,
                worst_observed_source_rate_pct: 100,
                complete_fresh: 100,
                states: {complete: 100, incomplete: 0, unknown: 0},
                complete_fresh_rate_pct: 100,
                objective_status: 'collecting',
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.reported, true);
    assert.equal(model.verifiedCompleteness.sourceCatalogCoverage, '100%');
    assert.equal(model.verifiedCompleteness.instrumentedObservationCoverage, '100%');
    assert.equal(model.verifiedCompleteness.attemptCoverage, '100%');
    assert.equal(model.verifiedCompleteness.completeFreshRate, '100%');
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
});

test('does not show met while the schedule ledger is still collecting', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        methodology: {
            combined_slo_readiness: 'collecting_pipeline_schedule_ledger',
        },
        default_window: '7d',
        windows: [{
            ...observedWindow,
            verified_completeness: {
                reported: true,
                instrumented_sources: 1,
                catalog_sources: 1,
                source_catalog_coverage_pct: 100,
                observed_instrumented_sources: 1,
                instrumented_source_observation_coverage_pct: 100,
                sources_meeting_target: 1,
                sources_below_target: 0,
                sources_without_observations: 0,
                source_target_attainment_pct: 100,
                macro_complete_fresh_rate_pct: 100,
                macro_target_met: true,
                worst_observed_source_rate_pct: 100,
                tracked_attempts: 100,
                complete_fresh: 100,
                states: {complete: 100, incomplete: 0, unknown: 0},
                coverage_of_all_parser_attempts_pct: 100,
                complete_fresh_rate_pct: 100,
                target_rate_pct: 99,
                objective_status: 'met',
            },
        }],
    });

    assert.equal(model.observed, false);
    assert.equal(model.preliminary, true);
    assert.equal(model.verifiedCompleteness.reported, false);
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
    assert.doesNotMatch(model.verifiedCompleteness.objectiveLabel, /met|достигнута/i);
});

test('does not confirm observed or met values from a stale cache', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        stale_cache: true,
        methodology: {
            combined_slo_readiness: 'ready',
        },
        default_window: '7d',
        windows: [{
            ...observedWindow,
            verified_completeness: {
                reported: true,
                instrumented_sources: 1,
                catalog_sources: 1,
                source_catalog_coverage_pct: 100,
                observed_instrumented_sources: 1,
                instrumented_source_observation_coverage_pct: 100,
                sources_meeting_target: 1,
                sources_below_target: 0,
                sources_without_observations: 0,
                source_target_attainment_pct: 100,
                macro_complete_fresh_rate_pct: 100,
                macro_target_met: true,
                worst_observed_source_rate_pct: 100,
                tracked_attempts: 100,
                complete_fresh: 100,
                states: {complete: 100, incomplete: 0, unknown: 0},
                coverage_of_all_parser_attempts_pct: 100,
                complete_fresh_rate_pct: 100,
                target_rate_pct: 99,
                objective_status: 'met',
            },
        }],
    });

    assert.equal(model.observed, false);
    assert.equal(model.preliminary, true);
    assert.equal(model.verifiedCompleteness.reported, false);
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
});

test('uses exact counts when a sub-99 rate rounds to 99 percent for display', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '30d',
        windows: [{
            ...observedWindow,
            window: '30d',
            eligible_attempts: 20_000,
            verified_completeness: {
                reported: true,
                instrumented_sources: 1,
                catalog_sources: 1,
                source_catalog_coverage_pct: 100,
                observed_instrumented_sources: 1,
                instrumented_source_observation_coverage_pct: 100,
                sources_meeting_target: 0,
                sources_below_target: 1,
                sources_without_observations: 0,
                source_target_attainment_pct: 0,
                macro_complete_fresh_rate_pct: 99,
                macro_target_met: false,
                worst_observed_source_rate_pct: 99,
                tracked_attempts: 20_000,
                complete_fresh: 19_799,
                states: {complete: 19_799, incomplete: 201, unknown: 0},
                coverage_of_all_parser_attempts_pct: 100,
                complete_fresh_rate_pct: 99,
                target_rate_pct: 99,
                objective_status: 'miss',
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.reported, true);
    assert.equal(model.verifiedCompleteness.completeFreshRate, '99%');
    assert.equal(model.verifiedCompleteness.macroTargetMet, false);
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'miss');
});

test('misses when weighted attempts reach 99.01% but one of two sources fails', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '30d',
        windows: [{
            ...observedWindow,
            window: '30d',
            eligible_attempts: 101,
            verified_completeness: {
                reported: true,
                instrumented_sources: 2,
                catalog_sources: 2,
                source_catalog_coverage_pct: 100,
                observed_instrumented_sources: 2,
                instrumented_source_observation_coverage_pct: 100,
                sources_meeting_target: 1,
                sources_below_target: 1,
                sources_without_observations: 0,
                source_target_attainment_pct: 50,
                macro_complete_fresh_rate_pct: 50,
                macro_target_met: false,
                worst_observed_source_rate_pct: 0,
                tracked_attempts: 101,
                complete_fresh: 100,
                states: {complete: 100, incomplete: 1, unknown: 0},
                coverage_of_all_parser_attempts_pct: 100,
                complete_fresh_rate_pct: 99.01,
                target_rate_pct: 99,
                objective_status: 'miss',
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.reported, true);
    assert.equal(model.verifiedCompleteness.completeFreshRate, '99,01%');
    assert.equal(model.verifiedCompleteness.sourcesMeetingTarget, 1);
    assert.equal(model.verifiedCompleteness.sourceTargetAttainment, '50%');
    assert.equal(model.verifiedCompleteness.macroCompleteFreshRate, '50%');
    assert.equal(model.verifiedCompleteness.worstObservedSourceRate, '0%');
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'miss');
});

test('keeps a weighted 99.49% slice as miss when the exact macro gate is below target', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '30d',
        windows: [{
            ...observedWindow,
            window: '30d',
            eligible_attempts: 19_801,
            verified_completeness: {
                reported: true,
                instrumented_sources: 100,
                catalog_sources: 100,
                source_catalog_coverage_pct: 100,
                observed_instrumented_sources: 100,
                instrumented_source_observation_coverage_pct: 100,
                sources_meeting_target: 99,
                sources_below_target: 1,
                sources_without_observations: 0,
                source_target_attainment_pct: 99,
                macro_complete_fresh_rate_pct: 98.51,
                macro_target_met: false,
                worst_observed_source_rate_pct: 0,
                tracked_attempts: 19_801,
                complete_fresh: 19_701,
                states: {complete: 19_701, incomplete: 100, unknown: 0},
                coverage_of_all_parser_attempts_pct: 100,
                complete_fresh_rate_pct: 99.49,
                target_rate_pct: 99,
                objective_status: 'miss',
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.reported, true);
    assert.equal(model.verifiedCompleteness.completeFreshRate, '99,49%');
    assert.equal(model.verifiedCompleteness.sourceTargetAttainment, '99%');
    assert.equal(model.verifiedCompleteness.macroCompleteFreshRate, '98,51%');
    assert.equal(model.verifiedCompleteness.macroTargetMet, false);
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'miss');
});

test('fails closed when the exact macro gate is missing or contradicts a clear sub-99 macro rate', () => {
    const withoutGate = structuredClone(observedWindow);
    delete withoutGate.verified_completeness.macro_target_met;
    const missingModel = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [withoutGate],
    });

    const contradictoryModel = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            verified_completeness: {
                ...observedWindow.verified_completeness,
                macro_target_met: true,
            },
        }],
    });

    assert.equal(missingModel.verifiedCompleteness.reported, false);
    assert.equal(missingModel.verifiedCompleteness.objectiveStatus, 'collecting');
    assert.equal(contradictoryModel.verifiedCompleteness.reported, false);
    assert.equal(contradictoryModel.verifiedCompleteness.objectiveStatus, 'collecting');
});

test('renders consistent collecting rates as an explicitly preliminary slice', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '24h',
        windows: [{
            ...observedWindow,
            window: '24h',
            measurement_status: 'collecting',
            rates_observed: false,
            rates_available: true,
            coverage_ratio: 0.25,
            full_fresh_rate_pct: 100,
            accepted_fresh_rate_pct: 100,
            data_available_rate_pct: 100,
        }],
    });

    assert.equal(model.badge, 'Предварительный срез');
    assert.equal(model.preliminary, true);
    assert.equal(model.fullFresh, '100%');
    assert.equal(model.availability, '100%');
    assert.equal(model.acceptedFresh, '100%');
    assert.equal(model.coverage, '25%');
});

test('uses the API daily default instead of forcing the incomplete weekly window', () => {
    const dailyWindow = {
        ...observedWindow,
        window: '24h',
        measurement_status: 'collecting',
        rates_observed: false,
        rates_available: true,
        full_fresh_rate_pct: 96.77,
        data_available_rate_pct: 99.8,
    };
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '24h',
        windows: [observedWindow, dailyWindow],
    });

    assert.equal(model.selectedWindow, '24h');
    assert.equal(model.fullFresh, '96,77%');
    assert.equal(model.availability, '99,8%');
    assert.equal(model.counts.lkg, 5);
});

test('suppresses inconsistent or otherwise unavailable preliminary rates', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '24h',
        windows: [{
            ...observedWindow,
            window: '24h',
            measurement_status: 'collecting',
            rates_observed: false,
            rates_available: false,
            full_fresh_rate_pct: 100,
            data_available_rate_pct: 100,
        }],
    });

    assert.equal(model.badge, 'Накапливаем статистику');
    assert.equal(model.fullFresh, '—');
    assert.equal(model.availability, '—');
});

test('shows a collecting state when the endpoint has no usable windows', () => {
    const model = buildReliabilityViewModel({
        state: 'collecting',
        windows: [],
    });

    assert.equal(model.badge, 'Накапливаем статистику');
    assert.equal(model.hasWindow, false);
    assert.equal(model.fullFresh, '—');
    assert.equal(model.availability, '—');
    assert.doesNotMatch(JSON.stringify(model), /100%/);
});

test('shows insufficient observations when the completeness block is absent', () => {
    const windowWithoutCompleteness = {...observedWindow};
    delete windowWithoutCompleteness.verified_completeness;
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [windowWithoutCompleteness],
    });

    assert.equal(model.verifiedCompleteness.reported, false);
    assert.equal(model.verifiedCompleteness.completeFreshRate, '—');
    assert.equal(model.verifiedCompleteness.attemptCoverage, '—');
    assert.equal(model.verifiedCompleteness.sourceCatalogCoverage, '—');
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
    assert.match(model.verifiedCompleteness.objectiveLabel, /Недостаточно наблюдений/);
    assert.doesNotMatch(
        JSON.stringify(model.verifiedCompleteness),
        /completeFreshRate":"100%|coverage":"100%/
    );
});

test('fails closed when verified completeness counts contradict each other', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            verified_completeness: {
                ...observedWindow.verified_completeness,
                states: {complete: 98, incomplete: 1, unknown: 0},
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.reported, false);
    assert.equal(model.verifiedCompleteness.completeFreshRate, '—');
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
});

test('fails closed when source rollout percentage contradicts its counts', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            verified_completeness: {
                ...observedWindow.verified_completeness,
                source_catalog_coverage_pct: 4.04,
            },
        }],
    });

    assert.equal(model.verifiedCompleteness.reported, false);
    assert.equal(model.verifiedCompleteness.sourceCatalogCoverage, '—');
    assert.equal(model.verifiedCompleteness.objectiveStatus, 'collecting');
});

test('does not treat the removed exact status as observed', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '7d',
        windows: [{
            ...observedWindow,
            measurement_status: 'exact',
            rates_observed: true,
            full_fresh_rate_pct: 100,
            data_available_rate_pct: 100,
        }],
    });

    assert.equal(model.badge, 'Накапливаем статистику');
    assert.equal(model.fullFresh, '—');
    assert.equal(model.availability, '—');
});
