import test from 'node:test';
import assert from 'node:assert/strict';
import {
  analyticsRequest,
  maxPage,
  missingHint,
  pageNumber,
  responseMessage,
} from '../lib/analytics-query.ts';

const card = { params: { card_name: { type: 'string', required: true } } };
const meta = { params: { format: { type: 'enum' } } };

test('card without a name is not sent to the backend', () => {
  const result = analyticsRequest(new URLSearchParams('action=analytics&stats=card'), 'card', card);
  assert.deepEqual(result.missing, ['card_name']);
  assert.equal(missingHint(result.missing), 'Введите название карты.');
});

test('the inspector button parameter q becomes card_name for the card module', () => {
  const result = analyticsRequest(
    new URLSearchParams('action=analytics&stats=card&q=Матриарх'),
    'card',
    card,
  );
  assert.deepEqual(result.missing, []);
  assert.equal(result.backend.toString(), new URLSearchParams('card_name=Матриарх&module=card').toString());
  assert.equal(result.page.get('card_name'), 'Матриарх');
  assert.equal(result.page.has('q'), false);
});

test('stats_q reaches card_name and an explicit card_name wins over q', () => {
  const fromStats = analyticsRequest(new URLSearchParams('stats=card&stats_q=Lich'), 'card', card);
  assert.equal(fromStats.backend.get('card_name'), 'Lich');
  const explicit = analyticsRequest(new URLSearchParams('stats=card&card_name=Lich&q=Other'), 'card', card);
  assert.equal(explicit.backend.get('card_name'), 'Lich');
  assert.equal(explicit.backend.has('q'), false);
});

test('other modules keep stats_q as q and drop client-only table filters', () => {
  const result = analyticsRequest(
    new URLSearchParams('action=analytics&stats=meta&stats_q=wild&rows_q=x&rows=5&format=wild'),
    'meta',
    meta,
  );
  assert.equal(result.backend.toString(), new URLSearchParams('format=wild&q=wild&module=meta').toString());
  assert.deepEqual(result.missing, []);
});

test('a blank card name counts as missing', () => {
  const result = analyticsRequest(new URLSearchParams('stats=card&card_name=%20%20'), 'card', card);
  assert.deepEqual(result.missing, ['card_name']);
});

test('response message comes from the adapter or falls back to a neutral text', () => {
  assert.equal(
    responseMessage({ ok: false, message: ' Не удалось загрузить статистику. ' }, 502),
    'Не удалось загрузить статистику.',
  );
  assert.equal(responseMessage('<html>', 500), 'Не удалось загрузить статистику (код 500).');
  assert.equal(responseMessage(null, 502), 'Не удалось загрузить статистику (код 502).');
  assert.equal(responseMessage({ message: 42 }, 422), 'Не удалось загрузить статистику (код 422).');
});

test('page becomes limit and offset for a module that pages, and never reaches other modules', () => {
  const paged = { params: { limit: { type: 'int' }, offset: { type: 'int' } } };
  const third = analyticsRequest(
    new URLSearchParams('stats=decks&page=3&offset=9&limit=500'),
    'decks',
    paged,
  );
  assert.equal(third.backend.get('limit'), '25');
  assert.equal(third.backend.get('offset'), '50');
  assert.equal(third.backend.has('page'), false);
  const first = analyticsRequest(new URLSearchParams('stats=decks&page=abc'), 'decks', paged);
  assert.equal(first.backend.get('offset'), '0');
  const flat = analyticsRequest(new URLSearchParams('stats=meta&page=3&offset=5'), 'meta', meta);
  assert.equal(flat.backend.has('offset'), false);
  assert.equal(flat.backend.has('page'), false);
});

test('page stays inside what the backend offset allows (default 10000, or the registry max)', () => {
  assert.equal(maxPage(), 401);
  assert.equal(maxPage({ params: { offset: { max: 100 } } }), 5);
  for (const bad of [null, '', 'abc', '0', '-3', 'NaN', 'Infinity'])
    assert.equal(pageNumber(bad), 1, String(bad));
  assert.equal(pageNumber('1.9'), 1);
  assert.equal(pageNumber('400'), 400);
  assert.equal(pageNumber('401'), 401);
  assert.equal(pageNumber('402'), 401);
  assert.equal(pageNumber('100000'), 401);
  assert.equal(pageNumber('9', 5), 5);
});

test('a page past the offset limit asks for the last servable offset and says so in the address', () => {
  const paged = { params: { limit: { type: 'int' }, offset: { type: 'int', max: 10000 } } };
  const far = analyticsRequest(new URLSearchParams('stats=decks&page=402'), 'decks', paged);
  assert.equal(far.backend.get('offset'), '10000');
  assert.equal(far.page.get('page'), '401');
  const edge = analyticsRequest(new URLSearchParams('stats=decks&page=401'), 'decks', paged);
  assert.equal(edge.backend.get('offset'), '10000');
  assert.equal(edge.page.get('page'), '401');
  const small = { params: { offset: { type: 'int', max: 100 } } };
  assert.equal(analyticsRequest(new URLSearchParams('page=50'), 'x', small).backend.get('offset'), '100');
});
