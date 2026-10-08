// Table columns per catalogue category. Field names follow the MySQL tables that next-data.php returns
// with SELECT * (panel/sql/schema.mysql.sql). The first column (thumbnail and name) is shared and
// rendered by Catalog; everything here is pure so it is unit-tested.
import { cardTypeLabels, classLabel, rarityLabels, slugLabel, type Tone, tribeLabels } from './format.ts';
import { decode, record, text } from './model.ts';
import type { Row } from './types.ts';

export type Cell =
  | { kind: 'text' | 'number' | 'code' | 'day'; value: string }
  | { kind: 'badge'; value: string; tone: Tone }
  | { kind: 'check'; value: boolean }
  | { kind: 'time'; value: unknown }
  | { kind: 'pills'; value: string[] };
export type Column = { key: string; label: string; numeric?: boolean; cell: (row: Row) => Cell };

const LIBRARY_TYPES = ['anomaly', 'quest', 'darkmoon_prize', 'reward', 'trinket'];

const blank = (value: unknown) => value === null || value === undefined || String(value).trim() === '';
const firstOf = (row: Row, fields: string[]) => {
  const key = fields.find(field => !blank(row[field]));
  return key ? String(row[key]) : '';
};

const textColumn = (key: string, label: string, value: (row: Row) => string): Column => ({
  key,
  label,
  cell: row => ({ kind: 'text', value: value(row) }),
});
const numberColumn = (key: string, label: string, ...fields: string[]): Column => ({
  key,
  label,
  numeric: true,
  cell: row => ({ kind: 'number', value: firstOf(row, fields.length ? fields : [key]) }),
});
const codeColumn = (key: string, label: string): Column => ({
  key,
  label,
  cell: row => ({ kind: 'code', value: firstOf(row, [key]) }),
});
const checkColumn = (key: string, label: string, value: (row: Row) => unknown): Column => ({
  key,
  label,
  cell: row => ({ kind: 'check', value: Boolean(value(row)) }),
});

/** Name of a related record stored as JSON (hero power, buddy). */
const relatedName = (value: unknown) => {
  const related = record(decode(value));
  return text(related.name_ru || related.name || related.name_en, '');
};
const listSize = (value: unknown) => {
  const list = decode(value);
  return Array.isArray(list) && list.length ? String(list.length) : '';
};
const label = (table: Record<string, string>, value: unknown) =>
  blank(value) ? '' : slugLabel(table, value);

const cardId = codeColumn('card_id', 'Card ID');
const dbf = codeColumn('dbf', 'DBF');
const updated: Column = {
  key: 'updated',
  label: 'Обновлено',
  cell: row => ({ kind: 'time', value: row.updated_at || row.changed_at || row.fetched_at }),
};
const released: Column = {
  key: 'release_date',
  label: 'Выход',
  cell: row => ({ kind: 'day', value: firstOf(row, ['release_date']) }),
};
const pool: Column = {
  key: 'in_pool',
  label: 'Пул',
  cell: row => {
    if (blank(row.in_pool)) return { kind: 'text', value: '' };
    return Number(row.in_pool) === 1
      ? { kind: 'badge', value: 'В пуле', tone: 'good' }
      : { kind: 'badge', value: 'Вне пула', tone: 'neutral' };
  },
};
const attack = numberColumn('attack', 'Атака');
const health = numberColumn('health', 'Здоровье');

function battlegrounds(cardType: string, tribes: Record<string, string>): Column[] {
  const kind = textColumn('type', 'Тип', row => {
    const tribe = text(row.creature_type, '');
    if (tribe) return tribes[tribe] || slugLabel(tribeLabels, tribe);
    return label(cardTypeLabels, row.card_type || cardType || 'minion');
  });
  const golden = checkColumn(
    'golden',
    'Золотая',
    row => record(row.golden_variant).card_id || row.golden_image,
  );
  const duos: Column = {
    key: 'duos_only',
    label: 'Режим',
    cell: row =>
      Number(row.duos_only) === 1
        ? { kind: 'badge', value: 'Только дуо', tone: 'info' }
        : { kind: 'text', value: '' },
  };
  const tier = numberColumn('tavern_tier', 'Уровень');
  if (cardType === 'spell') return [cardId, tier, pool, duos, golden, updated];
  return [cardId, kind, tier, attack, health, pool, duos, golden, updated];
}

const categories: Record<string, () => Column[]> = {
  hero: () => [
    cardId,
    numberColumn('armor', 'Броня'),
    numberColumn('duos_armor', 'Броня в дуо'),
    textColumn('hero_power', 'Сила героя', row => relatedName(row.hero_power_json)),
    textColumn('buddy', 'Напарник', row => relatedName(row.buddy_json)),
    {
      key: 'skins',
      label: 'Скины',
      numeric: true,
      cell: row => ({ kind: 'number', value: listSize(row.hero_skins_json) }),
    },
    updated,
  ],
  hero_skin: () => [
    cardId,
    textColumn(
      'class',
      'Класс',
      row =>
        firstOf(row, ['class_name_ru']) ||
        (blank(row.class_slug) ? firstOf(row, ['class_name_en']) : classLabel(row.class_slug)),
    ),
    textColumn('character_name', 'Персонаж', row => firstOf(row, ['character_name'])),
    textColumn('rarity', 'Редкость', row => firstOf(row, ['rarity_name_ru', 'rarity_name_en'])),
    textColumn('category', 'Категория', row => firstOf(row, ['primary_category_ru', 'primary_category_en'])),
    checkColumn('animated', 'Анимация', row => row.animated_image_url),
    released,
    updated,
  ],
  pet: () => [
    textColumn('pet_name', 'Питомец', row => firstOf(row, ['pet_name'])),
    numberColumn('level', 'Уровень'),
    cardId,
    dbf,
    checkColumn('background', 'Фон', row => row.end_screen_background_url),
    released,
    updated,
  ],
  coin: () => [
    cardId,
    textColumn('name_en', 'Название EN', row => firstOf(row, ['coin_name_en', 'card_name_en'])),
    textColumn('artist', 'Художник', row => firstOf(row, ['artist'])),
    checkColumn('golden', 'Золотая', row => row.image_gold_url),
    numberColumn('cosmetic_sort_order', 'Порядок'),
    released,
    updated,
  ],
  timewarped: () => [
    cardId,
    textColumn('type', 'Тип', row => {
      const tribe = firstOf(row, ['minion_type', 'race']);
      return tribe ? slugLabel(tribeLabels, tribe) : label(cardTypeLabels, row.card_type);
    }),
    numberColumn('tavern_tier', 'Таверна'),
    numberColumn('cost', 'Стоимость'),
    attack,
    health,
    checkColumn('golden', 'Золотая', row => row.golden_card_id || row.golden_image_url),
    updated,
  ],
  constructed: () => [
    cardId,
    textColumn('class', 'Класс', row => (blank(row.class_slug) ? '' : classLabel(row.class_slug))),
    textColumn('type', 'Тип', row => label(cardTypeLabels, row.card_type)),
    numberColumn('mana_cost', 'Мана'),
    attack,
    health,
    textColumn('rarity', 'Редкость', row => label(rarityLabels, row.rarity)),
    textColumn('card_set', 'Набор', row => firstOf(row, ['card_set'])),
    {
      key: 'formats',
      label: 'Форматы',
      cell: row => ({
        kind: 'pills',
        value: String(row.formats ?? '')
          .split(',')
          .filter(Boolean)
          .map(format => (format === 'standard' ? 'Стандарт' : format === 'wild' ? 'Вольный' : format)),
      }),
    },
    updated,
  ],
};

const library = (): Column[] => [
  cardId,
  textColumn('tier', 'Тир', row => firstOf(row, ['tier_name_ru', 'tier_value', 'tier_slug'])),
  textColumn('group', 'Группа', row => firstOf(row, ['group_name_ru', 'group_slug'])),
  numberColumn('mana_cost', 'Стоимость'),
  pool,
  updated,
];

export function catalogColumns(cardType: string, tribes: Record<string, string> = {}): Column[] {
  if (LIBRARY_TYPES.includes(cardType)) return library();
  return categories[cardType]?.() ?? battlegrounds(cardType, tribes);
}
