import test from 'node:test';
import assert from 'node:assert/strict';
import { analyticsRequest, missingHint, responseMessage } from '../lib/analytics-query.ts';

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
