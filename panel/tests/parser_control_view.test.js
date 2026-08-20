'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const view = require('../assets/parser-control-view.js');

const snapshot = {
    sections: [{
        id: 'meta', label: 'Мета', enabled: true, sources: [
            { id: 'ok', health: 'ok', rowsTotal: 120, nextRunAt: '2026-08-13T02:00:00Z' },
            { id: 'warn', health: 'warning', rowsTotal: 20, nextRunAt: '2026-08-13T01:00:00Z' },
        ],
    }],
    activeRun: { id: 'run-1', status: 'running' },
};

test('flattens sources while preserving section context', () => {
    const sources = view.flattenSources(snapshot);
    assert.equal(sources.length, 2);
    assert.equal(sources[0].sectionLabel, 'Мета');
    assert.equal(sources[0].sectionEnabled, true);
});

test('builds action-oriented parser summary', () => {
    const summary = view.buildSummary(snapshot);
    assert.equal(summary.total, 2);
    assert.equal(summary.healthy, 2);
    assert.equal(summary.issues, 1);
    assert.equal(summary.fresh, 1);
    assert.equal(summary.fallback, 1);
    assert.equal(summary.unavailable, 0);
    assert.equal(summary.rows, 140);
    assert.equal(summary.nextRunAt, '2026-08-13T01:00:00Z');
    assert.equal(summary.activeRun.id, 'run-1');
});

test('separates fresh data, fallback data, and a failure without data', () => {
    const fresh = view.sourcePresentation({ health: 'ok', rowsTotal: 53 });
    const fallback = view.sourcePresentation({
        health: 'warning',
        servingCachedDataset: true,
        rowsTotal: 53,
        lastSuccessAt: '2026-08-13T01:00:00Z',
        lastError: 'origin timeout',
    });
    const unavailable = view.sourcePresentation({
        health: 'error', rowsTotal: 0, publicationChannel: 'unavailable',
    });
    const disabled = view.sourcePresentation({ health: 'ok', enabled: false, rowsTotal: 53 });

    assert.deepEqual(
        { key: fresh.key, filter: fresh.filter, dataAvailable: fresh.dataAvailable },
        { key: 'fresh', filter: 'fresh', dataAvailable: true },
    );
    assert.deepEqual(
        { key: fallback.key, filter: fallback.filter, attemptFailed: fallback.attemptFailed },
        { key: 'fallback', filter: 'fallback', attemptFailed: true },
    );
    assert.deepEqual(
        { key: unavailable.key, filter: unavailable.filter, dataAvailable: unavailable.dataAvailable },
        { key: 'unavailable', filter: 'unavailable', dataAvailable: false },
    );
    assert.equal(disabled.key, 'disabled');
});

test('turns repeated transport diagnostics into a concise operator message', () => {
    const raw = 'curl_cffi: quality check failed: source contract failed: row_retrieval has unexplained dropped rows; flaresolverr: repeated failure';

    assert.equal(
        view.errorSummary(raw),
        'Проверка полноты обнаружила необъяснённые пропуски строк.',
    );
    assert.equal(view.errorSummary('origin timeout'), 'Источник не ответил за отведённое время.');
    assert.equal(view.publicationLabel('stable_baseline'), 'Стабильный резерв');
});

test('treats confirmed upstream publication wait as operationally healthy', () => {
    const waiting = {
        sections: [{
            id: 'matchups', label: 'Матчапы', enabled: true, sources: [
                { id: 'radars', health: 'upstream_pending', rowsTotal: 21 },
            ],
        }],
    };

    const summary = view.buildSummary(waiting);

    assert.deepEqual(view.statusMeta('upstream_pending'), {
        key: 'upstream_pending', label: 'Ожидает публикации', tone: 'info',
    });
    assert.equal(summary.healthy, 1);
    assert.equal(summary.issues, 0);
});

test('formats state and run progress without optimistic defaults', () => {
    assert.deepEqual(view.statusMeta('failed'), { key: 'failed', label: 'Ошибка', tone: 'bad' });
    assert.deepEqual(view.runProgress({ totalSources: 4, completedSources: 2, failedSources: 1 }), {
        total: 4, done: 3, percent: 75,
    });
    assert.equal(view.runProgress({}).percent, 0);
});

test('formats relative and exact UTC timestamps', () => {
    const date = view.formatDate('2026-08-13T01:00:00Z', Date.parse('2026-08-13T00:00:00Z'));
    assert.equal(date.relative, 'через 1 час');
    assert.match(date.exact, /UTC$/);
    assert.equal(view.formatDate('broken'), '—');
});
