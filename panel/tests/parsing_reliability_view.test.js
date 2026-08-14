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
    total_attempts: 105,
    counts: {
        fresh_published: 88,
        provisional: 4,
        lkg_served: 5,
        failed: 2,
        timed_out: 1,
        skipped: 5,
    },
    full_fresh_rate_pct: 88,
    accepted_fresh_rate_pct: 92,
    data_available_rate_pct: 97,
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

test('describes extraction evidence without claiming full upstream pages', () => {
    const renderer = fs.readFileSync(
        path.join(__dirname, '../assets/analytics.js'),
        'utf8'
    );

    assert.match(renderer, /Проверенная полнота извлечения/);
    assert.match(renderer, /Свежие ответы без потерь извлечения/);
    assert.match(renderer, /Полнота каталога upstream/);
    assert.match(renderer, /Rollout источников/);
    assert.match(renderer, /weighted по попыткам/);
    assert.match(renderer, /Источники, выполняющие 99%/);
    assert.match(renderer, /Macro rate по источникам/);
    assert.match(renderer, /Худший наблюдавшийся источник/);
    assert.doesNotMatch(renderer, /Полное получение данных|страница получена целиком/);
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
