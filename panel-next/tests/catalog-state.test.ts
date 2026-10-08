import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogHref, catalogView } from '../lib/catalog-state.ts';

test('changing collections keeps the search and view but clears unavailable filters', () => {
  const next = new URLSearchParams(
    catalogHref('q=dragon&view=tiles&page=4&tier=3&creature_type=dragon&pool=1&duos=1', {
      card_type: 'constructed',
    }).slice(2),
  );
  assert.equal(next.get('q'), 'dragon');
  assert.equal(next.get('view'), 'tiles');
  assert.equal(next.get('per_page'), '15');
  for (const key of ['page', 'tier', 'creature_type', 'pool', 'duos']) assert.equal(next.has(key), false);
});
test('ordinary filter updates retain compatible filters and reset pagination', () => {
  const next = new URLSearchParams(
    catalogHref('card_type=minion&view=list&tier=3&page=4&creature_type=beast', { pool: '1' }).slice(2),
  );
  assert.equal(next.get('tier'), '3');
  assert.equal(next.get('creature_type'), 'beast');
  assert.equal(next.get('pool'), '1');
  assert.equal(next.get('view'), 'list');
  assert.equal(next.has('page'), false);
});
test('an explicit page change preserves its destination and tile page size', () => {
  const next = new URLSearchParams(catalogHref('view=tiles&per_page=100&q=murloc', { page: 3 }).slice(2));
  assert.equal(next.get('page'), '3');
  assert.equal(next.get('per_page'), '15');
  assert.equal(next.get('q'), 'murloc');
});
test('switching back to Battlegrounds drops constructed-only filters', () => {
  const next = new URLSearchParams(
    catalogHref('card_type=constructed&constructed_format=wild&media=golden&rarity=rare', {
      card_type: 'minion',
    }).slice(2),
  );
  for (const key of ['constructed_format', 'media', 'rarity']) assert.equal(next.has(key), false);
  assert.equal(next.get('card_type'), 'minion');
});
test('all three explicit view modes are accepted and unknown values use cards', () => {
  assert.deepEqual(['grid', 'list', 'tiles', null, 'unknown'].map(catalogView), [
    'grid',
    'list',
    'tiles',
    'grid',
    'grid',
  ]);
});
