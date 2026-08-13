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
        const number = Number(value);
        if (!Number.isFinite(number)) return '—';
        return `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2}).format(number)}%`;
    };

    const nonNegativeCount = (value) => {
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? Math.trunc(number) : null;
    };

    const buildReliabilityViewModel = (reliability, selectedWindow = null) => {
        const windows = Array.isArray(reliability?.windows) ? reliability.windows : [];
        const requestedWindow = selectedWindow || reliability?.default_window || '24h';
        const window = windows.find((item) => item?.window === requestedWindow) || windows[0] || null;
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
            && window.measurement_status === 'observed'
            && window.rates_observed === true
        );
        const preliminary = ratesAvailable && !observed;
        const counts = window?.counts || {};
        const stale = reliability?.stale_cache === true;
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
            eligibleAttempts: window ? nonNegativeCount(window.eligible_attempts) : null,
            totalAttempts: window ? nonNegativeCount(window.total_attempts) : null,
            counts: {
                provisional: window ? nonNegativeCount(counts.provisional) : null,
                lkg: window ? nonNegativeCount(counts.lkg_served) : null,
                failed: window ? nonNegativeCount(counts.failed) : null,
                timedOut: window ? nonNegativeCount(counts.timed_out) : null,
                skipped: window ? nonNegativeCount(counts.skipped) : null,
            },
            generatedAt: reliability?.generated_at || null,
            message: reliability?.message || 'Накапливаем статистику',
        };
    };

    return {buildReliabilityViewModel};
}));
