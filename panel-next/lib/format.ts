// One place for how values read in the panel: dates, numbers, statuses and slugs.
// Pure functions only, so every section formats the same way and tests run without React.

export type Tone = 'good' | 'bad' | 'warning' | 'info' | 'neutral';
export type ValueKind =
  | 'text'
  | 'number'
  | 'percent'
  | 'date'
  | 'status'
  | 'boolean'
  | 'link'
  | 'image'
  | 'code'
  | 'card_type'
  | 'creature_type'
  | 'rarity';

const locale = 'ru-RU';
const numberFormatter = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
const dateTimeFormatter = new Intl.DateTimeFormat(locale, {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  timeZone: 'UTC',
});
const dayFormatter = new Intl.DateTimeFormat(locale, {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
});
const relativeFormatter = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' });
const pluralRules = new Intl.PluralRules(locale);

const isBlank = (value: unknown) => value === null || value === undefined || value === '';

export function parseDate(value: unknown): Date | null {
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value : null;
  if (typeof value === 'number' && Number.isFinite(value)) {
    // PHP stores some moments as Unix seconds; JavaScript timestamps are milliseconds.
    return new Date(value < 1e12 ? value * 1000 : value);
  }
  if (typeof value !== 'string' || !value.trim()) return null;
  const raw = value.trim();
  // MariaDB "YYYY-MM-DD HH:MM:SS" carries no zone; the stack stores UTC.
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}(:\d{2})?$/.test(raw) ? `${raw.replace(' ', 'T')}Z` : raw;
  const date = new Date(iso);
  return Number.isFinite(date.getTime()) ? date : null;
}

export function formatDateTime(value: unknown): string {
  const date = parseDate(value);
  return date ? `${dateTimeFormatter.format(date)} UTC` : '—';
}

export function formatDay(value: unknown): string {
  const date = parseDate(value);
  return date ? dayFormatter.format(date) : '—';
}

export function formatRelative(value: unknown, now: number = Date.now()): string {
  const date = parseDate(value);
  if (!date) return '—';
  const seconds = (date.getTime() - now) / 1000;
  const distance = Math.abs(seconds);
  if (distance < 45) return 'только что';
  if (distance < 45 * 60) return relativeFormatter.format(Math.round(seconds / 60), 'minute');
  if (distance < 22 * 3600) return relativeFormatter.format(Math.round(seconds / 3600), 'hour');
  if (distance < 26 * 86400) return relativeFormatter.format(Math.round(seconds / 86400), 'day');
  return formatDay(date);
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)))
    return Number(value);
  return null;
}

export function formatNumber(value: unknown): string {
  const number = toNumber(value);
  return number === null ? (isBlank(value) ? '—' : String(value)) : numberFormatter.format(number);
}

// Statistics already arrive in percent units (52.3 means 52.3%), as in the PHP panel.
export function formatPercent(value: unknown): string {
  const number = toNumber(value);
  if (number !== null) return `${numberFormatter.format(number)}%`;
  if (isBlank(value)) return '—';
  const raw = String(value);
  return raw.includes('%') ? raw : `${raw}%`;
}

export function plural(value: number, forms: { one: string; few: string; many: string }): string {
  const form = pluralRules.select(value);
  return form === 'one' ? forms.one : form === 'few' ? forms.few : forms.many;
}

export function countLabel(value: number, forms: { one: string; few: string; many: string }): string {
  return `${numberFormatter.format(value)} ${plural(value, forms)}`;
}

export function yesNo(value: unknown): string {
  if (isBlank(value)) return '—';
  const normalized = String(value).toLowerCase();
  return ['1', 'true', 'yes', 'да'].includes(normalized) ? 'Да' : 'Нет';
}

export const cardTypeLabels: Record<string, string> = {
  minion: 'Существо',
  spell: 'Заклинание',
  battleground_spell: 'Заклинание таверны',
  tavern_spell: 'Заклинание таверны',
  hero: 'Герой',
  hero_power: 'Сила героя',
  hero_skin: 'Скин героя',
  weapon: 'Оружие',
  location: 'Локация',
  buddy: 'Напарник',
  pet: 'Питомец',
  coin: 'Монетка',
  trinket: 'Аксессуар',
  anomaly: 'Аномалия',
  quest: 'Квест',
  reward: 'Награда',
  darkmoon_prize: 'Приз Ярмарки',
  timewarped: 'Хрономальная карта',
  constructed: 'Стандарт / Вольный',
};

// Same wording as creature_types() in panel/api/index.php.
export const tribeLabels: Record<string, string> = {
  aberration: 'Аберрация',
  all: 'Общие',
  undead: 'Нежить',
  dragon: 'Дракон',
  mech: 'Механизм',
  mechanical: 'Механизм',
  murloc: 'Мурлок',
  demon: 'Демон',
  quilboar: 'Свинобраз',
  naga: 'Нага',
  pirate: 'Пират',
  beast: 'Зверь',
  elemental: 'Элементаль',
};

export const rarityLabels: Record<string, string> = {
  free: 'Базовая',
  common: 'Обычная',
  rare: 'Редкая',
  epic: 'Эпическая',
  legendary: 'Легендарная',
};

export const scopeLabels: Record<string, string> = {
  'database:read': 'Чтение базы',
  admin: 'Управление API',
  'tokens:manage': 'Управление токенами',
};

export function slugLabel(table: Record<string, string>, value: unknown): string {
  if (isBlank(value)) return '—';
  const raw = String(value);
  return table[raw.toLowerCase()] ?? raw;
}

const statuses: Record<string, [string, Tone]> = {
  ok: ['В норме', 'good'],
  success: ['Успешно', 'good'],
  succeeded: ['Успешно', 'good'],
  fresh: ['Свежие данные', 'good'],
  available: ['Доступна', 'good'],
  ready: ['Готово', 'good'],
  matched: ['Совпадает', 'good'],
  active: ['Активен', 'good'],
  warning: ['Внимание', 'warning'],
  cached: ['Из кэша', 'warning'],
  stale: ['Устарело', 'warning'],
  provisional: ['Предварительно', 'warning'],
  fallback: ['Резервный набор', 'warning'],
  lkg: ['Резервный набор', 'warning'],
  degraded: ['Частично', 'warning'],
  partial: ['Частично', 'warning'],
  error: ['Ошибка', 'bad'],
  failed: ['Ошибка', 'bad'],
  failure: ['Ошибка', 'bad'],
  missing: ['Нет данных', 'bad'],
  unavailable: ['Недоступно', 'bad'],
  lock_timeout: ['Ждал блокировку', 'bad'],
  revoked: ['Отозван', 'bad'],
  expired: ['Истёк', 'bad'],
  running: ['Выполняется', 'info'],
  queued: ['В очереди', 'info'],
  pending: ['Ожидает', 'info'],
  preview: ['Анонс', 'info'],
  removed: ['Убрана', 'neutral'],
  skipped: ['Пропущено', 'neutral'],
  paused: ['Пауза', 'neutral'],
  disabled: ['Отключено', 'neutral'],
  not_due: ['Не по расписанию', 'neutral'],
};

export function statusMeta(value: unknown): { label: string; tone: Tone } {
  if (isBlank(value)) return { label: '—', tone: 'neutral' };
  const raw = String(value);
  const known = statuses[raw.toLowerCase()];
  if (known) return { label: known[0], tone: known[1] };
  // Unknown values keep their wording; the tone follows the PHP panel's heuristics.
  const normalized = raw.toLowerCase();
  const tone: Tone = /error|fail|проблем|ошиб/.test(normalized)
    ? 'bad'
    : /cache|stale|устар|последн|резерв/.test(normalized)
      ? 'warning'
      : 'neutral';
  return { label: raw, tone };
}

const tones: Tone[] = ['good', 'bad', 'warning', 'info', 'neutral'];
export function toneOf(value: unknown): Tone {
  const raw = String(value ?? '').toLowerCase();
  if ((tones as string[]).includes(raw)) return raw as Tone;
  if (['success', 'ok', 'positive'].includes(raw)) return 'good';
  if (['danger', 'error', 'negative', 'critical'].includes(raw)) return 'bad';
  if (['warn', 'attention'].includes(raw)) return 'warning';
  return 'neutral';
}

const fieldLabels: Record<string, string> = {
  id: 'ID записи',
  name: 'Название',
  name_ru: 'Название RU',
  name_en: 'Название EN',
  name_russian: 'Название RU',
  card_id: 'Card ID',
  dbf: 'DBF',
  dbf_id: 'DBF',
  tavern_tier: 'Уровень таверны',
  attack: 'Атака',
  health: 'Здоровье',
  armor: 'Броня',
  mana_cost: 'Мана',
  creature_type: 'Тип существа',
  card_type: 'Категория',
  rarity: 'Редкость',
  card_set: 'Набор',
  artist: 'Художник',
  in_pool: 'В пуле',
  duos_only: 'Только дуо',
  collectible: 'Коллекционная',
  notes: 'Описание',
  text_ru: 'Текст RU',
  text_en: 'Текст EN',
  status: 'Состояние',
  state: 'Состояние',
  source: 'Источник',
  source_id: 'Источник',
  updated_at: 'Обновлено',
  created_at: 'Создано',
  changed_at: 'Изменено',
  fetched_at: 'Последнее обновление',
  first_seen_at: 'Впервые замечена',
  last_seen_at: 'Последний импорт',
  golden_variant: 'Золотая версия',
  card_image: 'Карта',
  golden_image: 'Золотая карта',
  art_image: 'Арт',
  framed_image: 'Арт в рамке',
  horizontal_image_url: 'Горизонтальный арт',
  image_url: 'Изображение',
  rows: 'Строк',
};

export function fieldLabel(key: string): string {
  if (fieldLabels[key]) return fieldLabels[key];
  const words = key.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toLocaleUpperCase(locale) + words.slice(1) : key;
}

// Infers how to show a field when the backend gives no column type; mirrors panel/assets/analytics.js.
export function inferKind(key: string, value?: unknown): ValueKind {
  const name = key.toLowerCase();
  if (['card_type'].includes(name)) return 'card_type';
  if (['creature_type', 'minion_type', 'race'].includes(name)) return 'creature_type';
  if (name === 'rarity') return 'rarity';
  if (['status', 'state', 'availability_status', 'health_state'].includes(name)) return 'status';
  if (/^(in_pool|duos_only|collectible|is_[a-z_]+|has_[a-z_]+)$/.test(name) || typeof value === 'boolean')
    return 'boolean';
  if (/(image|art|portrait|crop|thumbnail)(_url)?$/.test(name)) return 'image';
  if (/(?:^|_)(?:url|link)$/.test(name)) return 'link';
  if (/date|_at$|fetched/.test(name)) return 'date';
  if (/rate|winrate|popularity|percentage|share|first_place/.test(name)) return 'percent';
  // Identifiers are copied verbatim; digit grouping would make "69000" read as "69 000".
  if (/(^|_)(id|card_id|dbf|dbf_id|hash|sha256|code)$/.test(name)) return name === 'id' ? 'text' : 'code';
  if (typeof value === 'number') return 'number';
  return 'text';
}

// Plain-text rendering for any kind; components add markup (badges, links, images) on top.
export function formatValue(kind: ValueKind, value: unknown): string {
  if (isBlank(value)) return '—';
  switch (kind) {
    case 'number':
      return formatNumber(value);
    case 'percent':
      return formatPercent(value);
    case 'date':
      return formatDateTime(value);
    case 'status':
      return statusMeta(value).label;
    case 'boolean':
      return yesNo(value);
    case 'card_type':
      return slugLabel(cardTypeLabels, value);
    case 'creature_type':
      return slugLabel(tribeLabels, value);
    case 'rarity':
      return slugLabel(rarityLabels, value);
    default:
      return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
}

// Statistics filters: the wording of panel/partials/analytics-dashboard.php.
const paramLabels: Record<string, string> = {
  format: 'Формат',
  format_name: 'Формат',
  rank: 'Ранг',
  period: 'Период',
  mode: 'Режим',
  rating: 'Рейтинг игроков',
  arena_source: 'Режим / источник',
  card_rank: 'Рейтинг карт',
  card_period: 'Период карт',
  card_name: 'Карта',
  class_name: 'Класс',
  q: 'Поиск',
  limit: 'Записей',
  min_games: 'Минимум игр',
  offset: 'Смещение',
  order: 'Порядок',
  sort: 'Сортировка',
  tavern_tier: 'Уровень таверны',
};

const optionLabels: Record<string, Record<string, string>> = {
  format: { standard: 'Standard', wild: 'Wild' },
  mode: { solo: 'Solo', duos: 'Duos' },
  rank: {
    all: 'Все',
    diamond: 'Diamond',
    diamond_4to1: 'Diamond 4–1',
    diamond_to_legend: 'Diamond – Legend',
    legend: 'Legend',
    top_5k: 'Top 5K',
    top_legend: 'Top Legend',
    top_500: 'Top 500',
    top_100: 'Top 100',
  },
  rating: { '100': 'Все игроки', '50': 'Top 50%', '25': 'Top 25%', '10': 'Top 10%', '1': 'Top 1%' },
  arena_source: {
    firestone: 'Обычная · Firestone',
    hsreplay: 'Обычная · HSReplay',
    underground: 'Подпольная · Firestone',
  },
  card_rank: { platinum: 'Platinum', diamond: 'Diamond', diamond_4_1: 'Diamond 4–1', legend: 'Legend' },
  card_period: { '1d': '24 часа', '3d': '3 дня', '7d': '7 дней', '14d': '14 дней', patch: 'Текущий патч' },
  order: { asc: 'По возрастанию', desc: 'По убыванию' },
  sort: { games: 'Игры', winrate: 'Винрейт', popularity: 'Популярность', name: 'Название' },
};

export function paramLabel(key: string): string {
  return paramLabels[key] ?? fieldLabel(key);
}

export function optionLabel(param: string, value: string): string {
  return optionLabels[param]?.[value] ?? value;
}

// Reliability windows arrive as "24h" / "7d".
export function windowLabel(value: string): string {
  const match = /^(\d+)([hd])$/.exec(value);
  if (!match) return value;
  const amount = Number(match[1]);
  return match[2] === 'h'
    ? countLabel(amount, { one: 'час', few: 'часа', many: 'часов' })
    : countLabel(amount, { one: 'день', few: 'дня', many: 'дней' });
}
