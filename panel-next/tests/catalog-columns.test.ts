import test from 'node:test';
import assert from 'node:assert/strict';
import { catalogColumns } from '../lib/catalog-columns.ts';
import { normalizedCard } from '../lib/model.ts';

const labels = (cardType: string) => catalogColumns(cardType).map(column => column.label);
const cells = (cardType: string, row: Record<string, unknown>) =>
  Object.fromEntries(catalogColumns(cardType).map(column => [column.key, column.cell(row)]));

test('every category gets its own columns', () => {
  assert.deepEqual(labels(''), [
    'Card ID',
    'Тип',
    'Уровень',
    'Атака',
    'Здоровье',
    'Пул',
    'Режим',
    'Золотая',
    'Обновлено',
  ]);
  assert.deepEqual(labels('spell'), ['Card ID', 'Уровень', 'Пул', 'Режим', 'Золотая', 'Обновлено']);
  assert.ok(labels('hero').includes('Сила героя'));
  assert.ok(labels('hero_skin').includes('Персонаж'));
  assert.ok(labels('pet').includes('Питомец'));
  assert.ok(labels('coin').includes('Художник'));
  assert.ok(labels('timewarped').includes('Таверна'));
  assert.ok(labels('constructed').includes('Форматы'));
  for (const library of ['anomaly', 'quest', 'darkmoon_prize', 'reward', 'trinket'])
    assert.deepEqual(labels(library), ['Card ID', 'Тир', 'Группа', 'Стоимость', 'Пул', 'Обновлено']);
});

test('battlegrounds cells keep zero stats and read pool, duos and golden flags', () => {
  const row = cells('', {
    card_id: 'BG_1',
    creature_type: 'murloc',
    tavern_tier: 1,
    attack: 0,
    health: 2,
    in_pool: 0,
    duos_only: 1,
    golden_variant: { card_id: 'BG_1_G' },
    updated_at: '2026-10-02T12:00:00Z',
  });
  assert.deepEqual(row.card_id, { kind: 'code', value: 'BG_1' });
  assert.deepEqual(row.type, { kind: 'text', value: 'Мурлок' });
  assert.deepEqual(row.attack, { kind: 'number', value: '0' });
  assert.deepEqual(row.in_pool, { kind: 'badge', value: 'Вне пула', tone: 'neutral' });
  assert.deepEqual(row.duos_only, { kind: 'badge', value: 'Только дуо', tone: 'info' });
  assert.deepEqual(row.golden, { kind: 'check', value: true });
  assert.deepEqual(row.updated, { kind: 'time', value: '2026-10-02T12:00:00Z' });
});

test('related JSON, formats and class slugs read as words', () => {
  const hero = cells('hero', {
    hero_power_json: '{"name_ru":"Чистка","name_en":"Purge"}',
    buddy_json: { name_en: 'Buddy' },
    hero_skins_json: '[{"card_id":"A"},{"card_id":"B"}]',
  });
  assert.deepEqual(hero.hero_power, { kind: 'text', value: 'Чистка' });
  assert.deepEqual(hero.buddy, { kind: 'text', value: 'Buddy' });
  assert.deepEqual(hero.skins, { kind: 'number', value: '2' });
  const card = cells('constructed', {
    class_slug: 'MAGE',
    card_type: 'SPELL',
    rarity: 'EPIC',
    formats: 'standard,wild',
  });
  assert.deepEqual(card.class, { kind: 'text', value: 'Маг' });
  assert.deepEqual(card.type, { kind: 'text', value: 'Заклинание' });
  assert.deepEqual(card.rarity, { kind: 'text', value: 'Эпическая' });
  assert.deepEqual(card.formats, { kind: 'pills', value: ['Стандарт', 'Вольный'] });
  assert.deepEqual(cells('constructed', {}).formats, { kind: 'pills', value: [] });
});

test('heroes, pets and coins get thumbnails and names from their own columns', () => {
  assert.equal(
    normalizedCard({ hero_image_url: 'https://example.test/hero.png' }, 'hero', {}).art,
    'https://example.test/hero.png',
  );
  assert.equal(
    normalizedCard({ card_image_url: '/uploads/pets/1.png' }, 'pet', {}).images[0],
    '/uploads/pets/1.png',
  );
  assert.equal(normalizedCard({ card_name_ru: 'Монетка', coin_name_en: 'Coin' }, 'coin', {}).name, 'Монетка');
  // Curated horizontal art still wins over the per-category fallbacks.
  assert.equal(
    normalizedCard(
      {
        horizontal_image_url: '/uploads/horizontal-art/H.webp',
        hero_full_art_url: 'https://example.test/full.png',
      },
      'hero',
      {},
    ).art,
    '/uploads/horizontal-art/H.webp',
  );
});
