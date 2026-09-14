'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');
const view = require('../assets/catalog-workspace-view.js');

test('signals only ordinary same-origin page navigation', () => {
    const currentUrl = 'https://api.kolodahearthstone.com/?page=1';

    assert.equal(view.shouldSignalNavigation({ href: '/?page=2' }, currentUrl), true);
    assert.equal(view.shouldSignalNavigation({ href: '#statistics' }, currentUrl), false);
    assert.equal(view.shouldSignalNavigation({ href: 'https://example.com/' }, currentUrl), false);
    assert.equal(view.shouldSignalNavigation({ href: '/?page=2', ctrlKey: true }, currentUrl), false);
    assert.equal(view.shouldSignalNavigation({ href: '/?page=2', target: '_blank' }, currentUrl), false);
});

test('normalizes a catalogue row into readable inspector data', () => {
    const record = view.recordFromDataset({
        recordId: '  BG31_500  ',
        recordName: ' Пылесос ',
        recordImage: '/uploads/cards/BG31_500.png',
        recordType: 'Существо',
        recordTier: '2',
        recordAttack: '3',
        recordHealth: '2',
        recordMechanics: 'Усиление | Боевой клич',
        recordUpdated: '2026-09-14 13:42 UTC',
        recordPool: 'В пуле',
        recordEditUrl: '/?action=edit&id=42',
        recordStatsUrl: '/?action=analytics&stats=card&stats_q=Vacuum',
    });

    assert.deepEqual(record.mechanics, ['Усиление', 'Боевой клич']);
    assert.equal(record.id, 'BG31_500');
    assert.equal(record.name, 'Пылесос');
    assert.equal(record.tier, '2');
    assert.equal(record.pool, 'В пуле');
    assert.equal(record.editUrl, '/?action=edit&id=42');
});

test('uses explicit, readable fallbacks for incomplete rows', () => {
    const record = view.recordFromDataset({ recordName: '' });

    assert.equal(record.name, 'Без названия');
    assert.equal(record.id, '—');
    assert.equal(record.image, '');
    assert.deepEqual(record.mechanics, []);
    assert.equal(record.updated, 'Нет данных');
});
