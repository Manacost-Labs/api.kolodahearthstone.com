import test from 'node:test';
import assert from 'node:assert/strict';
import { historyChanges, historyIdentity } from '../lib/card-history.ts';

test('comparison preserves zero, distinguishes missing from null and ignores polling bookkeeping', () => {
  const changes = historyChanges(
    { attack: 0, health: 2, text_ru: 'Старый текст', updated_at: 'yesterday', source_payload: { a: 1 } },
    {
      attack: 1,
      health: 2,
      text_ru: 'Новый текст',
      mana_cost: null,
      updated_at: 'today',
      source_payload: { a: 2 },
    },
  );
  assert.deepEqual(
    changes.map(change => change.key),
    ['attack', 'mana_cost', 'text_ru'],
  );
  assert.equal(changes[0].before, 0);
  assert.equal(changes[1].before, undefined);
  assert.equal(changes[1].after, null);
  assert.deepEqual(
    historyChanges({ stats: { attack: 1, health: 2 } }, { stats: { health: 2, attack: 1 } }),
    [],
  );
});
test('history identity isolates constructed, BG, golden and same-id library records', () => {
  assert.deepEqual(historyIdentity({ card_id: 'A' }, '/api/v1/constructed-cards'), {
    entity: 'constructed_card',
    cardId: 'A',
  });
  assert.deepEqual(historyIdentity({ card_id: 'A_G' }, '/api/v1/cards'), { entity: 'card', cardId: 'A_G' });
  assert.deepEqual(historyIdentity({ card_id: 'A' }, '/api/v1/libraries/reward'), {
    entity: 'library_card',
    cardId: 'reward:A',
  });
  assert.deepEqual(historyIdentity({ card_id: 'A', library: 'trinket' }, '/api/v1/libraries/trinket'), {
    entity: 'library_card',
    cardId: 'trinket:A',
  });
  assert.equal(historyIdentity({ card_id: 'A' }, '/other-service'), null);
  assert.equal(historyIdentity({}, '/api/v1/cards'), null);
});
