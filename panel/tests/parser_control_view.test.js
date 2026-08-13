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
    assert.equal(summary.healthy, 1);
    assert.equal(summary.issues, 1);
    assert.equal(summary.rows, 140);
    assert.equal(summary.nextRunAt, '2026-08-13T01:00:00Z');
    assert.equal(summary.activeRun.id, 'run-1');
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
