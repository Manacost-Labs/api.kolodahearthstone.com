import test from 'node:test';
import assert from 'node:assert/strict';
import { cardDetail, cardHref, cardKey, matchesCard, parseBattlegroundNotes } from '../lib/card-detail.ts';

test('battlegrounds notes split into Russian text, mechanics and English text', () => {
  assert.deepEqual(
    parseBattlegroundNotes(
      'Боевой клич: дать +1/+1.\r\nМеханики: Боевой клич, Золотая\nEN: Battlecry: Give +1/+1.',
    ),
    { ru: 'Боевой клич: дать +1/+1.', mechanics: ['Боевой клич', 'Золотая'], en: 'Battlecry: Give +1/+1.' },
  );
  assert.deepEqual(parseBattlegroundNotes(null), { ru: '', en: '', mechanics: [] });
});

test('a battlegrounds minion becomes a curated detail', () => {
  const detail = cardDetail(
    {
      id: 7,
      card_id: 'BG_1',
      dbf: 101,
      name: 'Мурлок-разведчик',
      name_en: 'Murloc Scout',
      card_type: 'minion',
      creature_type: 'murloc',
      tavern_tier: 1,
      attack: 0,
      health: 2,
      in_pool: 1,
      duos_only: 0,
      notes: 'Текст.\nМеханики: Боевой клич\nEN: Text.',
      card_image: '/uploads/cards/BG_1.png',
      art_image: '/uploads/art/BG_1.jpg',
      golden_image: '/uploads/golden/BG_1_G.png',
      golden_variant: { card_id: 'BG_1_G', dbf: 201 },
      updated_at: '2026-10-02T12:00:00Z',
    },
    '',
    {},
  );
  assert.equal(detail.name, 'Мурлок-разведчик');
  assert.equal(detail.nameEn, 'Murloc Scout');
  assert.deepEqual(
    detail.stats.map(stat => [stat.label, stat.value]),
    [
      ['Уровень', '1'],
      ['Атака', '0'],
      ['Здоровье', '2'],
    ],
  );
  assert.deepEqual(
    detail.badges.map(badge => badge.label),
    ['Существо', 'Мурлок', 'В пуле'],
  );
  assert.deepEqual(detail.text, { ru: 'Текст.', en: 'Text.', flavor: '' });
  assert.deepEqual(detail.mechanics, ['Боевой клич']);
  assert.deepEqual(
    detail.ids.map(id => id.value),
    ['BG_1', '101', 'BG_1_G', '201'],
  );
  assert.deepEqual(detail.images.normal, ['/uploads/cards/BG_1.png', '/uploads/art/BG_1.jpg']);
  assert.deepEqual(detail.images.golden, ['/uploads/golden/BG_1_G.png']);
});

test('constructed cards use their own text, mechanics and formats', () => {
  const detail = cardDetail(
    {
      card_id: 'CORE_1',
      dbf: 5,
      name_ru: 'Огненный шар',
      card_type: 'SPELL',
      rarity: 'COMMON',
      class_slug: 'mage',
      mana_cost: 4,
      text_ru: 'Наносит 6 ед. урона.',
      text_en: 'Deal 6 damage.',
      flavor_ru: 'Классика.',
      mechanics_json: '["Заклинание"]',
      formats: 'standard,wild',
    },
    'constructed',
    {},
  );
  assert.deepEqual(
    detail.stats.map(stat => stat.label),
    ['Стоимость'],
  );
  assert.deepEqual(
    detail.badges.map(badge => badge.label),
    ['Заклинание', 'Маг', 'Обычная', 'Стандарт', 'Вольный'],
  );
  assert.equal(detail.text.flavor, 'Классика.');
  assert.deepEqual(detail.mechanics, ['Заклинание']);
});

test('?card= finds a record by card id, internal id or DBF', () => {
  const row = { id: 7, card_id: 'BG_1', dbf: 101 };
  assert.equal(cardKey(row), 'BG_1');
  for (const key of ['BG_1', '7', '101']) assert.equal(matchesCard(row, key), true);
  assert.equal(matchesCard(row, 'BG_10'), false);
  assert.equal(matchesCard(row, ''), false);
});

test('palette links open a card inside its own category', () => {
  assert.equal(cardHref('', 'BG_1'), '/?card=BG_1');
  assert.equal(cardHref('hero', 'TB_BaconShop_HERO_01'), '/?card_type=hero&card=TB_BaconShop_HERO_01');
  assert.equal(cardHref('pet', 'variant:7&x'), '/?card_type=pet&card=variant%3A7%26x');
});
