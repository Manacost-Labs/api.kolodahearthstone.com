(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    } else {
        root.ParsingReliabilityUI = api;
    }
}(typeof globalThis !== 'undefined' ? globalThis : this, () => {
    'use strict';

    const percentage = (value) => {
        if (value === null || value === '' || typeof value === 'boolean') return '—';
        const number = Number(value);
        if (!Number.isFinite(number)) return '—';
        return `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(number)}%`;
    };

    const boundedPercentage = (value) => {
        if (value === null || value === '' || typeof value === 'boolean') return null;
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 && number <= 100 ? number : null;
    };

    const nonNegativeCount = (value) => {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? Math.trunc(number) : null;
    };

    const exactNonNegativeCount = (value) => {
        if (value === null || value === '' || typeof value === 'boolean') return null;
        const number = Number(value);
        return Number.isInteger(number) && number >= 0 ? number : null;
    };

    const buildScheduledReliability = (scheduled, stale = false) => {
        const fallback = {
            reported: false,
            ledgerStatus: 'absent',
            measurementStatus: 'collecting',
            observed: false,
            preliminary: false,
            onTimeFreshRate: '—',
            rateAvailable: false,
            targetRate: '99%',
            scheduleCoverage: '—',
            temporalCoverage: '—',
            coverageStartedAt: null,
            materializedThrough: null,
            trackedSchedules: null,
            catalogSchedules: null,
            expectedSlots: null,
            eligibleSlots: null,
            excludedSlots: null,
            pendingSlots: null,
            dueSlots: null,
            onTimeFresh: null,
            onTimeNonfresh: null,
            late: null,
            missing: null,
            objectiveStatus: 'collecting',
            objectiveLabel: 'Недостаточно данных расписания · collecting',
            objectiveClass: 'is-collecting',
        };
        if (!scheduled || scheduled.reported === false) return fallback;

        const ledgerStatus = String(scheduled.ledger_status || '');
        const measurementStatus = String(scheduled.measurement_status || '');
        const objectiveStatus = String(scheduled.objective_status || '');
        const boundedRatio = (value) => {
            if (value === null || value === '' || typeof value === 'boolean') return null;
            const number = Number(value);
            return Number.isFinite(number) && number >= 0 && number <= 1
                ? number
                : null;
        };
        const scheduleCoverage = boundedRatio(scheduled.schedule_coverage_ratio);
        const temporalCoverage = boundedRatio(scheduled.temporal_coverage_ratio);
        const target = boundedPercentage(scheduled.target_rate_pct);
        const countKeys = [
            'tracked_schedules', 'catalog_schedules', 'expected_slots',
            'eligible_slots', 'excluded_slots', 'pending_slots', 'due_slots',
            'on_time_fresh', 'on_time_nonfresh', 'late', 'missing',
        ];
        const counts = Object.fromEntries(countKeys.map((key) => [
            key,
            exactNonNegativeCount(scheduled[key]),
        ]));
        const countsPresent = Object.values(counts).every((value) => value !== null);
        const isoTimestamp = (value) => {
            if (typeof value !== 'string') return false;
            const match = value.match(
                /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$/
            );
            if (!match) return false;
            const [year, month, day, hour, minute, second] = match
                .slice(1, 7)
                .map(Number);
            const offsetHour = match[7] === undefined ? null : Number(match[7]);
            const offsetMinute = match[8] === undefined ? null : Number(match[8]);
            const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
            return month >= 1 && month <= 12
                && day >= 1 && day <= daysInMonth
                && hour <= 23 && minute <= 59 && second <= 59
                && (offsetHour === null || (offsetHour <= 23 && offsetMinute <= 59))
                && Number.isFinite(Date.parse(value));
        };
        const timestampsValid = isoTimestamp(scheduled.coverage_started_at)
            && isoTimestamp(scheduled.materialized_through)
            && Date.parse(scheduled.materialized_through)
                >= Date.parse(scheduled.coverage_started_at);
        const ratiosValid = scheduleCoverage !== null && temporalCoverage !== null;
        if (
            !['partial', 'covered'].includes(ledgerStatus)
            || !['collecting', 'observed'].includes(measurementStatus)
            || !['collecting', 'meeting', 'breached'].includes(objectiveStatus)
            || target === null
            || !countsPresent
            || !timestampsValid
            || !ratiosValid
        ) return fallback;

        const expectedScheduleCoverage = counts.catalog_schedules === 0
            ? 0
            : Math.round((counts.tracked_schedules / counts.catalog_schedules) * 10000) / 10000;
        const reportedRate = boundedPercentage(scheduled.on_time_fresh_rate_pct);
        const expectedRate = counts.due_slots === 0
            ? null
            : Math.round((counts.on_time_fresh / counts.due_slots) * 10000) / 100;
        const covered = scheduleCoverage === 1 && temporalCoverage === 1;
        let expectedObjective = 'collecting';
        if (measurementStatus === 'observed' && counts.due_slots > 0) {
            expectedObjective = counts.on_time_fresh * 100
                >= target * counts.due_slots ? 'meeting' : 'breached';
        }
        const valid = counts.tracked_schedules > 0
            && counts.catalog_schedules > 0
            && counts.tracked_schedules <= counts.catalog_schedules
            && Math.abs(scheduleCoverage - expectedScheduleCoverage) <= 0.00011
            && counts.expected_slots === counts.eligible_slots + counts.excluded_slots
            && counts.eligible_slots === counts.due_slots + counts.pending_slots
            && counts.due_slots === counts.on_time_fresh + counts.on_time_nonfresh
                + counts.late + counts.missing
            && (
                (expectedRate === null && reportedRate === null)
                || (expectedRate !== null && reportedRate !== null
                    && Math.abs(reportedRate - expectedRate) <= 0.011)
            )
            && (ledgerStatus === 'covered') === covered
            && !(measurementStatus === 'observed' && !covered)
            && !(measurementStatus === 'collecting' && objectiveStatus !== 'collecting')
            && objectiveStatus === expectedObjective;
        if (!valid) return fallback;

        const presentedStatus = stale ? 'collecting' : measurementStatus;
        const presentedObjective = stale ? 'collecting' : objectiveStatus;
        const objectiveLabels = {
            collecting: 'Собираем наблюдения · collecting',
            meeting: 'Цель выполняется · meeting',
            breached: 'Цель нарушена · breached',
        };
        const objectiveClasses = {
            collecting: 'is-collecting',
            meeting: 'is-met',
            breached: 'is-miss',
        };
        return {
            reported: true,
            ledgerStatus,
            measurementStatus: presentedStatus,
            observed: presentedStatus === 'observed',
            preliminary: ledgerStatus === 'partial' || presentedStatus !== 'observed',
            onTimeFreshRate: expectedRate === null ? '—' : percentage(reportedRate),
            rateAvailable: expectedRate !== null,
            targetRate: percentage(target),
            scheduleCoverage: percentage(scheduleCoverage * 100),
            temporalCoverage: percentage(temporalCoverage * 100),
            coverageStartedAt: scheduled.coverage_started_at,
            materializedThrough: scheduled.materialized_through,
            trackedSchedules: counts.tracked_schedules,
            catalogSchedules: counts.catalog_schedules,
            expectedSlots: counts.expected_slots,
            eligibleSlots: counts.eligible_slots,
            excludedSlots: counts.excluded_slots,
            pendingSlots: counts.pending_slots,
            dueSlots: counts.due_slots,
            onTimeFresh: counts.on_time_fresh,
            onTimeNonfresh: counts.on_time_nonfresh,
            late: counts.late,
            missing: counts.missing,
            objectiveStatus: presentedObjective,
            objectiveLabel: objectiveLabels[presentedObjective],
            objectiveClass: objectiveClasses[presentedObjective],
        };
    };

    const buildVerifiedCompleteness = (
        verified,
        parentMeasurementStatus,
        allParserAttempts
    ) => {
        const fallback = {
            reported: false,
            hasObservations: false,
            rateAvailable: false,
            completeFreshRate: '—',
            attemptCoverage: '—',
            sourceCatalogCoverage: '—',
            instrumentedObservationCoverage: '—',
            targetRate: '99%',
            instrumentedSources: null,
            catalogSources: null,
            observedInstrumentedSources: null,
            sourcesMeetingTarget: null,
            sourcesBelowTarget: null,
            sourcesWithoutObservations: null,
            sourceTargetAttainment: '—',
            macroCompleteFreshRate: '—',
            macroTargetMet: null,
            worstObservedSourceRate: '—',
            trackedAttempts: null,
            completeFresh: null,
            states: {complete: null, incomplete: null, unknown: null},
            objectiveStatus: 'collecting',
            objectiveLabel: 'Недостаточно наблюдений · collecting',
            objectiveClass: 'is-collecting',
        };
        if (verified?.reported !== true) return fallback;

        const instrumentedSources = exactNonNegativeCount(verified.instrumented_sources);
        const catalogSources = exactNonNegativeCount(verified.catalog_sources);
        const sourceCatalogCoverage = boundedPercentage(
            verified.source_catalog_coverage_pct
        );
        const observedInstrumentedSources = exactNonNegativeCount(
            verified.observed_instrumented_sources
        );
        const instrumentedObservationCoverage = boundedPercentage(
            verified.instrumented_source_observation_coverage_pct
        );
        const sourcesMeetingTarget = exactNonNegativeCount(
            verified.sources_meeting_target
        );
        const sourcesBelowTarget = exactNonNegativeCount(
            verified.sources_below_target
        );
        const sourcesWithoutObservations = exactNonNegativeCount(
            verified.sources_without_observations
        );
        const sourceTargetAttainment = boundedPercentage(
            verified.source_target_attainment_pct
        );
        const macroCompleteFreshRate = boundedPercentage(
            verified.macro_complete_fresh_rate_pct
        );
        const macroTargetMet = typeof verified.macro_target_met === 'boolean'
            ? verified.macro_target_met
            : null;
        const worstObservedSourceRate = boundedPercentage(
            verified.worst_observed_source_rate_pct
        );
        const trackedAttempts = exactNonNegativeCount(verified.tracked_attempts);
        const completeFresh = exactNonNegativeCount(verified.complete_fresh);
        const complete = exactNonNegativeCount(verified.states?.complete);
        const incomplete = exactNonNegativeCount(verified.states?.incomplete);
        const unknown = exactNonNegativeCount(verified.states?.unknown);
        const attemptCoverage = boundedPercentage(
            verified.coverage_of_all_parser_attempts_pct
        );
        const completeFreshRate = boundedPercentage(verified.complete_fresh_rate_pct);
        const target = boundedPercentage(verified.target_rate_pct);
        const objectiveStatus = String(verified.objective_status || '');
        const parentStatusValid = ['collecting', 'observed'].includes(
            parentMeasurementStatus
        );
        const eligibleAttempts = exactNonNegativeCount(allParserAttempts);
        const countsValid = [
            instrumentedSources,
            catalogSources,
            observedInstrumentedSources,
            sourcesMeetingTarget,
            sourcesBelowTarget,
            sourcesWithoutObservations,
            trackedAttempts,
            completeFresh,
            complete,
            incomplete,
            unknown,
            eligibleAttempts,
        ]
            .every((value) => value !== null)
            && instrumentedSources <= catalogSources
            && observedInstrumentedSources <= instrumentedSources
            && sourcesMeetingTarget + sourcesBelowTarget === observedInstrumentedSources
            && sourcesWithoutObservations === instrumentedSources - observedInstrumentedSources
            && trackedAttempts <= eligibleAttempts
            && completeFresh <= complete
            && sourcesMeetingTarget <= completeFresh
            && !(completeFresh === trackedAttempts && sourcesBelowTarget > 0)
            && complete + incomplete + unknown === trackedAttempts;
        const ratioMatches = (numerator, denominator, reported) => {
            if (denominator === 0) return numerator === 0 && reported === null;
            const expected = Math.round((numerator / denominator) * 10000) / 100;
            return reported !== null && Math.abs(reported - expected) <= 0.011;
        };
        const coverageCountsValid = countsValid
            && ratioMatches(instrumentedSources, catalogSources, sourceCatalogCoverage)
            && ratioMatches(
                observedInstrumentedSources,
                instrumentedSources,
                instrumentedObservationCoverage
            )
            && ratioMatches(
                sourcesMeetingTarget,
                instrumentedSources,
                sourceTargetAttainment
            )
            && ratioMatches(trackedAttempts, eligibleAttempts, attemptCoverage);
        const macroBoundsValid = (() => {
            if (!countsValid) return false;
            if (instrumentedSources === 0) {
                return macroCompleteFreshRate === null
                    && worstObservedSourceRate === null;
            }
            if (macroCompleteFreshRate === null) return false;
            const lower = (sourcesMeetingTarget * 99) / instrumentedSources;
            const upper = (
                (sourcesMeetingTarget * 100) + (sourcesBelowTarget * 99)
            ) / instrumentedSources;
            if (
                macroCompleteFreshRate + 0.011 < lower
                || macroCompleteFreshRate - 0.011 > upper
                || macroCompleteFreshRate - 0.011 > instrumentedObservationCoverage
            ) return false;
            if (observedInstrumentedSources === 0) {
                return macroCompleteFreshRate === 0 && worstObservedSourceRate === null;
            }
            if (worstObservedSourceRate === null) return false;
            const observedMean = (macroCompleteFreshRate * instrumentedSources)
                / observedInstrumentedSources;
            return worstObservedSourceRate - 0.011 <= observedMean
                && !(sourcesBelowTarget === 0 && worstObservedSourceRate < 99)
                && !(sourcesBelowTarget > 0 && worstObservedSourceRate > 99);
        })();
        const emptyValid = trackedAttempts === 0
            && completeFresh === 0
            && completeFreshRate === null
            && objectiveStatus === 'collecting';
        const observedValid = trackedAttempts > 0
            && completeFreshRate !== null
            && ratioMatches(completeFresh, trackedAttempts, completeFreshRate);
        const allCoverageGatesMet = catalogSources > 0
            && instrumentedSources * 100 >= 99 * catalogSources
            && instrumentedSources > 0
            && observedInstrumentedSources * 100 >= 99 * instrumentedSources
            && eligibleAttempts > 0
            && trackedAttempts * 100 >= 99 * eligibleAttempts;
        const sourceTargetGateMet = instrumentedSources > 0
            && sourcesMeetingTarget * 100 >= 99 * instrumentedSources;
        const macroTargetGateValid = macroTargetMet !== null
            && (!macroTargetMet || (
                macroCompleteFreshRate !== null
                && macroCompleteFreshRate + 0.011 >= 99
            ));
        const expectedObjective = parentMeasurementStatus !== 'observed'
            || !allCoverageGatesMet
            || completeFreshRate === null
            ? 'collecting'
            : (
                completeFresh * 100 >= 99 * trackedAttempts
                    && sourceTargetGateMet
                    && macroTargetMet
                    ? 'met'
                    : 'miss'
            );
        if (
            !coverageCountsValid
            || !macroBoundsValid
            || !macroTargetGateValid
            || !parentStatusValid
            || target !== 99
            || (!emptyValid && !observedValid)
            || objectiveStatus !== expectedObjective
        ) return fallback;

        const objectiveLabels = {
            collecting: 'Собираем наблюдения · collecting',
            met: 'Цель достигнута · met',
            miss: 'Цель не достигнута · miss',
        };
        return {
            reported: true,
            hasObservations: trackedAttempts > 0,
            rateAvailable: completeFreshRate !== null,
            completeFreshRate: percentage(completeFreshRate),
            attemptCoverage: percentage(attemptCoverage),
            sourceCatalogCoverage: percentage(sourceCatalogCoverage),
            instrumentedObservationCoverage: percentage(
                instrumentedObservationCoverage
            ),
            targetRate: percentage(target),
            instrumentedSources,
            catalogSources,
            observedInstrumentedSources,
            sourcesMeetingTarget,
            sourcesBelowTarget,
            sourcesWithoutObservations,
            sourceTargetAttainment: percentage(sourceTargetAttainment),
            macroCompleteFreshRate: percentage(macroCompleteFreshRate),
            macroTargetMet,
            worstObservedSourceRate: percentage(worstObservedSourceRate),
            trackedAttempts,
            completeFresh,
            states: {complete, incomplete, unknown},
            objectiveStatus,
            objectiveLabel: objectiveLabels[objectiveStatus],
            objectiveClass: `is-${objectiveStatus}`,
        };
    };

    const buildReliabilityViewModel = (reliability, selectedWindow = null) => {
        const windows = Array.isArray(reliability?.windows) ? reliability.windows : [];
        const requestedWindow = selectedWindow || reliability?.default_window || '24h';
        const window = windows.find((item) => item?.window === requestedWindow) || windows[0] || null;
        const readiness = reliability?.methodology?.combined_slo_readiness;
        const readinessAllowsObserved = readiness === undefined || readiness === 'ready';
        const stale = reliability?.stale_cache === true;
        const measurementStatus = !stale
            && window?.measurement_status === 'observed'
            && readinessAllowsObserved
            ? 'observed'
            : 'collecting';
        const measurementStatusValid = Boolean(
            window
            && ['collecting', 'observed'].includes(window.measurement_status)
        );
        const ratesAvailable = Boolean(
            measurementStatusValid
            && window.rates_available === true
        );
        const observed = Boolean(
            ratesAvailable
            && measurementStatus === 'observed'
            && window.rates_observed === true
        );
        const preliminary = ratesAvailable && !observed;
        const counts = window?.counts || {};
        const verifiedCompleteness = buildVerifiedCompleteness(
            window?.verified_completeness,
            measurementStatus,
            window?.eligible_attempts
        );
        const scheduledReliability = buildScheduledReliability(
            window?.scheduled_reliability,
            stale
        );
        const badge = observed
            ? 'Наблюдаемый срез'
            : (preliminary ? 'Предварительный срез' : 'Накапливаем статистику');

        return {
            hasWindow: Boolean(window),
            selectedWindow: window?.window || requestedWindow,
            windows: windows.map((item) => item.window).filter(Boolean),
            badge: stale && ratesAvailable ? `${badge} · кэш ответа` : badge,
            observed,
            preliminary,
            ratesAvailable,
            stale,
            fullFresh: ratesAvailable ? percentage(window.full_fresh_rate_pct) : '—',
            availability: ratesAvailable ? percentage(window.data_available_rate_pct) : '—',
            acceptedFresh: ratesAvailable ? percentage(window.accepted_fresh_rate_pct) : '—',
            coverage: window ? percentage(Number(window.coverage_ratio) * 100) : '—',
            observedEligibleAttempts: window ? nonNegativeCount(window.observed_eligible_attempts) : null,
            missingTerminalWindows: window ? nonNegativeCount(window.missing_terminal_windows) : null,
            eligibleAttempts: window ? nonNegativeCount(window.eligible_attempts) : null,
            totalAttempts: window ? nonNegativeCount(window.total_attempts) : null,
            counts: {
                provisional: window ? nonNegativeCount(counts.provisional) : null,
                lkg: window ? nonNegativeCount(counts.lkg_served) : null,
                failed: window ? nonNegativeCount(counts.failed) : null,
                timedOut: window ? nonNegativeCount(counts.timed_out) : null,
                skipped: window ? nonNegativeCount(counts.skipped) : null,
            },
            verifiedCompleteness,
            scheduledReliability,
            generatedAt: reliability?.generated_at || null,
            message: reliability?.message || 'Накапливаем статистику',
        };
    };

    return {buildReliabilityViewModel};
}));
