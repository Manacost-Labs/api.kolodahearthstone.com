// Curated view of one catalogue record for the card inspector. Pure, so it is unit-tested.
import { cardTypeLabels, classLabel, rarityLabels, slugLabel, type Tone, tribeLabels } from './format.ts';
import { decode, mediaUrl, record, text } from './model.ts';
import type { Row } from './types.ts';

export type Stat = { key: string; label: string; value: string };
export type Badge = { label: string; tone?: Tone };

const present = (value: unknown) => value !== null && value !== undefined && String(value).trim() !== '';

/** Battlegrounds `notes` hold "RU text\nМеханики: A, B\nEN: English text" (scan_cards.php build_notes). */
export function parseBattlegroundNotes(notes: unknown): { ru: string; en: string; mechanics: string[] } {
  const ru: string[] = [];
  let en = '';
  let mechanics: string[] = [];
  for (const line of String(notes ?? '').split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    if (/^Механики:/u.test(trimmed))
      mechanics = trimmed
        .replace(/^Механики:\s*/u, '')
        .split(',')
        .map(item => item.trim())
        .filter(Boolean);
    else if (/^EN:/.test(trimmed)) en = trimmed.replace(/^EN:\s*/, '');
    else ru.push(trimmed);
  }
  return { ru: ru.join('\n'), en, mechanics };
}

function stringList(value: unknown): string[] {
  const decoded = decode(value);
  if (!Array.isArray(decoded)) return [];
  return decoded
    .map(item => (typeof item === 'string' ? item : text(record(item).name || record(item).slug, '')))
    .filter(Boolean);
}

/** Identifier used in `?card=`; stable across reloads for every category. */
export function cardKey(row: Row): string {
  return text(row.card_id || row.hero_card_id || row.variant_id || row.id || row.dbf, '');
}

export function matchesCard(row: Row, key: string): boolean {
  if (!key) return false;
  return [row.card_id, row.hero_card_id, row.variant_id, row.id, row.dbf].some(
    value => present(value) && String(value) === key,
  );
}

/** Link that opens a card in its category, e.g. from the command palette. */
export function cardHref(cardType: string, key: string): string {
  const query = new URLSearchParams();
  if (cardType) query.set('card_type', cardType);
  query.set('card', key);
  return `/?${query}`;
}

const statFields: [string[], string][] = [
  [['tavern_tier', 'tier_value'], 'Уровень'],
  [['level'], 'Уровень'],
  [['mana_cost', 'cost'], 'Стоимость'],
  [['attack'], 'Атака'],
  [['health'], 'Здоровье'],
  [['armor'], 'Броня'],
  [['durability'], 'Прочность'],
];

export function cardDetail(row: Row, cardType: string, tribes: Record<string, string>) {
  const golden = record(row.golden_variant);
  const wiki = record(row.wiki);
  const notes = parseBattlegroundNotes(row.notes);
  const battlegrounds = ['', 'minion', 'spell'].includes(cardType);
  const ru = text(row.text_ru || (battlegrounds ? notes.ru : '') || row.hero_description, '');
  const en = text(row.text_en || (battlegrounds ? notes.en : ''), '');
  const mechanics = [
    ...new Set([
      ...notes.mechanics,
      ...stringList(row.mechanics_json),
      ...stringList(wiki.wiki_mechanics_json),
    ]),
  ];

  const stats: Stat[] = [];
  for (const [keys, label] of statFields) {
    const key = keys.find(field => present(row[field]));
    if (key && !stats.some(stat => stat.label === label)) stats.push({ key, label, value: String(row[key]) });
  }

  const typeSlug = text(row.card_type || cardType || 'minion', '').toLowerCase();
  const badges: Badge[] = [{ label: slugLabel(cardTypeLabels, typeSlug) }];
  const tribe = text(row.creature_type || row.minion_type || row.race, '');
  if (tribe) badges.push({ label: tribes[tribe] || slugLabel(tribeLabels, tribe) });
  if (present(row.class_name_ru || row.class_slug))
    badges.push({ label: present(row.class_name_ru) ? text(row.class_name_ru) : classLabel(row.class_slug) });
  if (present(row.rarity_name_ru || row.rarity))
    badges.push({ label: text(row.rarity_name_ru || slugLabel(rarityLabels, row.rarity)) });
  if (present(row.card_set)) badges.push({ label: text(row.card_set) });
  if (present(row.formats))
    for (const format of String(row.formats).split(','))
      badges.push({
        label: format === 'standard' ? 'Стандарт' : format === 'wild' ? 'Вольный' : format,
        tone: 'info',
      });
  if (present(row.in_pool))
    badges.push(
      Number(row.in_pool) === 1 ? { label: 'В пуле', tone: 'good' } : { label: 'Не в пуле', tone: 'neutral' },
    );
  if (Number(row.duos_only) === 1) badges.push({ label: 'Только дуо', tone: 'info' });

  const ids = [
    { label: 'Card ID', value: text(row.card_id || row.hero_card_id, '') },
    { label: 'DBF', value: text(row.dbf, '') },
    { label: 'Золотая · Card ID', value: text(golden.card_id || row.golden_card_id, '') },
    { label: 'Золотая · DBF', value: text(golden.dbf || row.golden_dbf, '') },
  ].filter(item => item.value);

  const unique = (urls: unknown[]) => [...new Set(urls.map(mediaUrl).filter(Boolean))];
  const images = {
    normal: unique([
      row.card_image,
      row.card_image_url,
      row.image_url,
      row.static_image_url,
      row.hero_image_url,
      row.local_image_url,
      row.art_image,
      row.full_art_url,
    ]),
    golden: unique([
      row.golden_image,
      golden.card_image,
      row.golden_image_url,
      row.image_gold_url,
      golden.art_image,
    ]),
  };

  return {
    name: text(
      row.name_ru || row.name || row.pet_name || row.card_name_ru || row.coin_name_en || row.name_en,
      'Без названия',
    ),
    nameEn: text(row.name_en || row.card_name_en, ''),
    badges,
    stats,
    text: { ru, en, flavor: text(row.flavor_ru || row.flavor_text, '') },
    mechanics,
    ids,
    images,
    wikiUrl: mediaUrl(wiki.wiki_page_url || row.wiki_page_url || row.page_url),
    artist: text(row.artist || wiki.artist, ''),
    updatedAt: row.updated_at || row.changed_at || row.fetched_at,
  };
}
