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
        recordInternalId: '42',
        recordId: '  BG31_500  ',
        recordDbf: '69042',
        recordName: ' Пылесос ',
        recordImage: '/uploads/cards/BG31_500.png',
        recordGoldenImage: '/uploads/cards/BG31_500_G.png',
        recordArtImage: '/uploads/art/BG31_500.png',
        recordGoldenArtImage: '/uploads/art/BG31_500_G.png',
        recordFramedImage: '/uploads/framed/BG31_500.png',
        recordGoldenFramedImage: '/uploads/framed/BG31_500_G.png',
        recordHorizontalImage: '/uploads/crops/BG31_500.jpg',
        recordGoldenHorizontalImage: '/uploads/crops/BG31_500_G.jpg',
        recordGoldenId: 'BG31_500_G',
        recordGoldenDbf: '69043',
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
    assert.deepEqual(record.images, [
        { kind: 'card', label: 'Обычная карта', url: '/uploads/cards/BG31_500.png' },
        { kind: 'golden', label: 'Золотая карта', url: '/uploads/cards/BG31_500_G.png' },
        { kind: 'art', label: 'Арт без рамки', url: '/uploads/art/BG31_500.png' },
        { kind: 'golden-art', label: 'Золотой арт', url: '/uploads/art/BG31_500_G.png' },
        { kind: 'framed', label: 'Арт в рамке', url: '/uploads/framed/BG31_500.png' },
        { kind: 'golden-framed', label: 'Золотой арт в рамке', url: '/uploads/framed/BG31_500_G.png' },
        { kind: 'horizontal', label: 'Горизонтальный арт', url: '/uploads/crops/BG31_500.jpg' },
        { kind: 'golden-horizontal', label: 'Золотой горизонтальный арт', url: '/uploads/crops/BG31_500_G.jpg' },
    ]);
    assert.deepEqual(record.identifiers, {
        internalId: '42',
        cardId: 'BG31_500',
        dbf: '69042',
        goldenCardId: 'BG31_500_G',
        goldenDbf: '69043',
    });
    assert.deepEqual(record.apiLinks, [
        { label: 'Обычная · card_id', value: 'BG31_500', url: '/api/v1/cards/BG31_500' },
        { label: 'Обычная · dbf', value: '69042', url: '/api/v1/cards/by-dbf/69042' },
        { label: 'Золотая · card_id', value: 'BG31_500_G', url: '/api/v1/cards/BG31_500_G' },
        { label: 'Золотая · dbf', value: '69043', url: '/api/v1/cards/by-dbf/69043' },
    ]);
});

test('uses explicit, readable fallbacks for incomplete rows', () => {
    const record = view.recordFromDataset({ recordName: '' });

    assert.equal(record.name, 'Без названия');
    assert.equal(record.id, '—');
    assert.equal(record.image, '');
    assert.deepEqual(record.images, []);
    assert.deepEqual(record.apiLinks, []);
    assert.deepEqual(record.mechanics, []);
    assert.equal(record.updated, 'Нет данных');
});
