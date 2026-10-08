import { cardTypeLabels, slugLabel, tribeLabels } from './format.ts';
import type { Row } from './types.ts';
export const text = (value: unknown, fallback = '—'): string =>
  value === null || value === undefined || value === ''
    ? fallback
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value);
export function decode(value: unknown): unknown {
  if (typeof value !== 'string') return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}
export function record(value: unknown): Row {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Row) : {};
}
export function mediaUrl(value: unknown): string {
  if (typeof value !== 'string' || !value) return '';
  if (value.startsWith('/') && !value.startsWith('//')) return value;
  try {
    const url = new URL(value);
    return ['https:', 'http:'].includes(url.protocol) ? value : '';
  } catch {
    return '';
  }
}
export function queryHref(query: string, changes: Record<string, string | number | null>): string {
  const next = new URLSearchParams(query);
  for (const [key, value] of Object.entries(changes)) {
    if (value === null || value === '') next.delete(key);
    else next.set(key, String(value));
  }
  return '/' + (next.size ? '?' + next.toString() : '');
}
export function normalizedCard(row: Row, cardType: string, tribes: Record<string, string>) {
  const name = text(
    row.name_ru ||
      row.name ||
      row.name_russian ||
      row.name_en ||
      row.pet_name_ru ||
      row.variant_name ||
      row.pet_name ||
      row.coin_name_ru ||
      row.coin_name_en,
    'Без названия',
  );
  const id = text(row.card_id || row.hero_card_id || row.skin_id || row.pet_id || row.id);
  const art = mediaUrl(
    row.art_image ||
      row.full_art_url ||
      row.local_crop_image_url ||
      row.crop_image_url ||
      row.horizontal_image_url ||
      row.local_image_url ||
      row.static_image_url ||
      row.card_image ||
      row.image_url ||
      row.portrait_url,
  );
  const tribe = row.creature_type
    ? tribes[text(row.creature_type, '')] || slugLabel(tribeLabels, row.creature_type)
    : slugLabel(cardTypeLabels, row.card_type || cardType || 'minion');
  const images = [art, mediaUrl(row.card_image || row.local_image_url || row.image_url || row.wiki_image_url)]
    .filter(Boolean)
    .filter((url, i, all) => all.indexOf(url) === i);
  return {
    row,
    id,
    name,
    art,
    images,
    tribe,
    tier: text(row.tavern_tier || row.tier, ''),
    attack: text(row.attack, ''),
    health: text(row.health, ''),
    inPool: Number(row.in_pool) === 1,
    golden: Boolean(record(row.golden_variant).card_id),
    editable:
      ['', 'minion', 'spell'].includes(cardType) && row.id !== undefined && Number.isFinite(Number(row.id)),
  };
}
export function entityApiBase(cardType: string): string {
  const endpoints: Record<string, string> = {
    hero: 'heroes',
    hero_skin: 'hero-skins',
    pet: 'pets',
    coin: 'coins',
    constructed: 'constructed-cards',
    timewarped: 'timewarped-cards',
  };
  return (
    '/api/v1/' +
    (endpoints[cardType] ||
      (['anomaly', 'quest', 'reward', 'darkmoon_prize', 'trinket'].includes(cardType)
        ? 'libraries/' + cardType
        : 'cards'))
  );
}

function tileNumber(value: unknown, minimum = 0): string {
  const number =
    typeof value === 'number' || (typeof value === 'string' && value.trim() !== '') ? Number(value) : NaN;
  return Number.isInteger(number) && number >= minimum ? String(number) : '—';
}
const tribeTileColors: Record<string, string> = {
  beast: '#49633f',
  demon: '#56375e',
  dragon: '#753f37',
  elemental: '#386074',
  mech: '#46586e',
  murloc: '#28675f',
  naga: '#3b477b',
  pirate: '#795839',
  quilboar: '#75475a',
  undead: '#59603c',
};
function battlegroundTilePalette(row: Row) {
  const values = [row.creature_types, row.races, row.creature_type, row.minion_type, row.race];
  const tribes = [
    ...new Set(
      values
        .flatMap(value => {
          const decoded = decode(value);
          return (Array.isArray(decoded) ? decoded : [decoded]).flatMap(item => {
            const slug = typeof item === 'object' ? record(item).slug : item;
            return typeof slug === 'string' ? slug.toLowerCase().split(/[\s,;/|+]+/) : [];
          });
        })
        .map(tribe => (tribe === 'mechanical' ? 'mech' : tribe === 'quillboar' ? 'quilboar' : tribe)),
    ),
  ].sort();
  if (tribes.includes('all'))
    return {
      color: '#605273',
      background: 'linear-gradient(110deg, #49633f, #28675f 35%, #75475a 70%, #605273)',
    };
  const colors = [
    ...new Set(
      tribes.filter(tribe => Object.hasOwn(tribeTileColors, tribe)).map(tribe => tribeTileColors[tribe]),
    ),
  ];
  const color = colors.at(-1) || '#606367';
  return { color, background: colors.length > 1 ? `linear-gradient(110deg, ${colors.join(', ')})` : color };
}
export function deckTile(row: Row, cardType = 'constructed') {
  const battlegrounds = ['', 'minion', 'spell', 'timewarped'].includes(cardType);
  const type = text(
    typeof row.card_type === 'object' ? record(row.card_type).slug : row.card_type,
    cardType || 'minion',
  ).toLowerCase();
  const spell = ['spell', 'battleground_spell', 'tavern_spell'].includes(type);
  const cost = battlegrounds
    ? tileNumber(row.tavern_tier ?? row.tier, 1)
    : tileNumber(row.mana_cost ?? row.cost);
  const rarity = String(row.rarity ?? '').toUpperCase();
  const legendary = rarity === 'LEGENDARY' || rarity === '5';
  const rarityColor = legendary
    ? '#715022'
    : ['EPIC', '4'].includes(rarity)
      ? '#503961'
      : ['RARE', '3'].includes(rarity)
        ? '#23445b'
        : '#606367';
  const { color, background } = battlegrounds
    ? spell
      ? { color: '#606367', background: '#606367' }
      : battlegroundTilePalette(row)
    : { color: rarityColor, background: rarityColor };
  const price = battlegrounds
    ? spell
      ? tileNumber(row.cost ?? row.mana_cost)
      : type === 'minion'
        ? '3'
        : ''
    : legendary
      ? '★'
      : '';
  return {
    cost,
    price,
    battlegrounds,
    color,
    background,
    legendary,
    image: mediaUrl(row.horizontal_image_url),
  };
}
export function imagesFrom(row: Row): { label: string; url: string }[] {
  const images: { label: string; url: string }[] = [];
  const seen = new Set<string>();
  function visit(value: unknown, name: string, depth: number) {
    if (depth > 5) return;
    if (typeof value === 'string') {
      const url = mediaUrl(value);
      if (
        url &&
        /(?:image|art|portrait|crop|file_url|animated|golden|framed|horizontal)/i.test(name) &&
        !/\.(?:mp3|ogg|wav)(?:\?|$)/i.test(url) &&
        !seen.has(url)
      ) {
        seen.add(url);
        images.push({ label: name, url });
      } else if (/^[\[{]/.test(value)) visit(decode(value), name, depth + 1);
    } else if (Array.isArray(value)) {
      for (const [i, v] of value.entries()) visit(v, name + ' ' + (i + 1), depth + 1);
    } else if (value && typeof value === 'object')
      for (const [key, v] of Object.entries(value)) visit(v, name ? name + ' · ' + key : key, depth + 1);
  }
  visit(row, '', 0);
  return images;
}
// panel/lib/api_tokens.php nests monthly usage under `usage`.
export function tokenUsage(token: Row): { requests: number; errors: number; month: string } {
  const usage = record(token.usage);
  const count = (value: unknown) =>
    typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0;
  return {
    requests: count(usage.request_count),
    errors: count(usage.error_count),
    month: text(usage.month, ''),
  };
}
