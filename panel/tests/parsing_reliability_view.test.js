'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const {
    buildReliabilityViewModel,
} = require('../assets/parsing-reliability.js');

const observedWindow = {
    window: '7d',
    measurement_status: 'observed',
    rates_observed: true,
    coverage_ratio: 1,
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
};

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
});

test('never renders a collecting 100 percent as an observed rate', () => {
    const model = buildReliabilityViewModel({
        state: 'available',
        default_window: '24h',
        windows: [{
            ...observedWindow,
            window: '24h',
            measurement_status: 'collecting',
            rates_observed: false,
            coverage_ratio: 0.25,
            full_fresh_rate_pct: 100,
            accepted_fresh_rate_pct: 100,
            data_available_rate_pct: 100,
        }],
    });

    assert.equal(model.badge, 'Накапливаем статистику');
    assert.equal(model.fullFresh, '—');
    assert.equal(model.availability, '—');
    assert.equal(model.acceptedFresh, '—');
    assert.equal(model.coverage, '25%');
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
