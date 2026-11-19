// The model of the «Сводка» page: four mode cards, each with the data date and the top three. Pure
// functions (no React, no server-only imports), so tests run as-is. The page asks the existing modules;
// nothing here needs a new API field.
import { archetypeIdentity, normalizeClass, type ClassSlug } from './archetypes.ts';
import { classLabel, formatPercent, formatTwoDecimals, parseDate } from './format.ts';
import type { AnalyticsData, Row } from './types.ts';

export type HomeCardKey = 'standard' | 'wild' | 'bg' | 'arena';

export type HomeCardDefinition = {
  key: HomeCardKey;
  title: string;
  /** Registry module the card reads. */
  module: string;
  /** Filters of the card's request (the module's own defaults for everything else). */
  params: Record<string, string>;
  /** The mode section the whole card opens. */
  href: string;
  open: string;
  /** Caption above the two numbers of a row. */
  legend: string;
};

export const homeCards: readonly HomeCardDefinition[] = [
  {
    key: 'standard',
    title: 'Стандарт',
    module: 'meta',
    params: { format: 'standard' },
    href: '/?action=analytics&stats=meta&format=standard',
    open: 'Открыть мету',
    legend: 'Доля · винрейт',
  },
  {
    key: 'wild',
    title: 'Вольный',
    module: 'meta',
    params: { format: 'wild' },
    href: '/?action=analytics&stats=meta&format=wild',
    open: 'Открыть мету',
    legend: 'Доля · винрейт',
  },
  {
    key: 'bg',
    title: 'Поля сражений',
    module: 'bg_heroes',
    params: { mode: 'solo' },
    href: '/?action=analytics&stats=bg_heroes',
    open: 'Открыть героев',
    legend: 'Выбор · среднее место',
  },
  {
    key: 'arena',
    title: 'Арена',
    module: 'arena',
    params: {},
    href: '/?action=analytics&stats=arena',
    open: 'Открыть классы',
    legend: 'Выбор · винрейт',
  },
];

export const TOP = 3;
/** Data older than this reads «устарело» on its card. */
export const staleAfterMs = 3 * 24 * 60 * 60 * 1000;

export type HomeRow = {
  rank: number;
  title: string;
  /** The English name under a Russian one. */
  subtitle: string | null;
  /** Null for a row without a class (Battlegrounds heroes have none): no icon column. */
  classSlug: ClassSlug | null;
  icon: boolean;
  /** First number (share / pick rate) and second (win rate / average placement), already formatted. */
  first: string;
  second: string;
  /** Win rate at or above 50 %: the second number is green. */
  up: boolean;
};

export type HomeCard = {
  key: HomeCardKey;
  title: string;
  href: string;
  open: string;
  legend: string;
  status: 'ok' | 'empty' | 'error';
  /** The ISO moment of the data, or null when the module did not say. */
  asOf: string | null;
  stale: boolean;
  message: string;
  rows: HomeRow[];
};

export type HomeResult = { data: AnalyticsData | null; message: string };

/** A number from `52.3`, `"7.98%"` or `"58,5 %"`; null for anything else (never 0 for a missing value). */
export function readNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const clean = value.replace(/[%\s ]/g, '').replace(',', '.');
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(clean)) return null;
  return Number(clean);
}

/** `summary` key `data_as_of` first (as in the meta page), then `meta.updated_at`. */
export function dataDate(data: AnalyticsData | null | undefined): string | null {
  if (!data) return null;
  const summary = data.summary?.find(item => item.key === 'data_as_of')?.value;
  for (const candidate of [summary, data.meta?.updated_at]) {
    const date = parseDate(candidate);
    if (date) return date.toISOString();
  }
  return null;
}

type Candidate = { sort: number; row: Omit<HomeRow, 'rank'> };
const dash = '—';
const place = (value: number | null) => (value === null ? dash : formatTwoDecimals(value));
const percent = (value: number | null) => (value === null ? dash : formatPercent(value));

function constructed(row: Row): Candidate | null {
  const share = readNumber(row.popularity ?? row.popularity_pct ?? row.share_pct);
  const winrate = readNumber(row.winrate ?? row.winrate_pct);
  if (share === null && winrate === null) return null;
  const identity = archetypeIdentity(row.archetype, row.class_name);
  return {
    sort: share ?? -Infinity,
    row: {
      title: identity.title,
      subtitle: identity.subtitle,
      classSlug: identity.classSlug,
      icon: true,
      first: percent(share),
      second: percent(winrate),
      up: (winrate ?? 0) >= 50,
    },
  };
}

function heroes(row: Row): Candidate | null {
  const pick = readNumber(row.pick_rate_value ?? row.share_pct);
  const placement = readNumber(row.avg_placement ?? row.avg_placement_value);
  const name = String(row.hero ?? row.name ?? '').trim();
  if (!name || (pick === null && placement === null)) return null;
  return {
    sort: pick ?? -Infinity,
    row: {
      title: name,
      subtitle: null,
      classSlug: null,
      icon: false,
      first: percent(pick),
      second: place(placement),
      up: false,
    },
  };
}

function arenaClass(row: Row): Candidate | null {
  const slug = normalizeClass(row.class_name) ?? normalizeClass(row.class_slug) ?? normalizeClass(row.class);
  const pick = readNumber(row.pick_rate);
  const winrate = readNumber(row.win_rate ?? row.winrate);
  if (!slug || (pick === null && winrate === null)) return null;
  return {
    sort: pick ?? winrate ?? -Infinity,
    row: {
      title: classLabel(slug),
      subtitle: null,
      classSlug: slug,
      icon: true,
      first: percent(pick),
      second: percent(winrate),
      up: (winrate ?? 0) >= 50,
    },
  };
}

const readers: Record<HomeCardKey, (row: Row) => Candidate | null> = {
  standard: constructed,
  wild: constructed,
  bg: heroes,
  arena: arenaClass,
};

/** The top three rows of a module's answer, best first; rows without any usable number are skipped. */
export function topRows(key: HomeCardKey, rows: Row[] | undefined): HomeRow[] {
  const read = readers[key];
  return (rows ?? [])
    .flatMap(row => {
      const item = read(row);
      return item ? [item] : [];
    })
    .map((item, index) => ({ ...item, index }))
    .sort((a, b) => b.sort - a.sort || a.index - b.index)
    .slice(0, TOP)
    .map(({ row }, index) => ({ ...row, rank: index + 1 }));
}

/** One card from one module's answer (or its failure). Never throws: the card shows what it got. */
export function buildCard(
  definition: HomeCardDefinition,
  result: HomeResult,
  now: number = Date.now(),
): HomeCard {
  const base = {
    key: definition.key,
    title: definition.title,
    href: definition.href,
    open: definition.open,
    legend: definition.legend,
  };
  if (!result.data) {
    return { ...base, status: 'error', asOf: null, stale: false, message: result.message, rows: [] };
  }
  const asOf = dataDate(result.data);
  const stale = asOf !== null && now - Date.parse(asOf) > staleAfterMs;
  // Arena legend without the pick rate in the answer: only the win rate is shown.
  const rows = topRows(definition.key, result.data.rows);
  const legend =
    definition.key === 'arena' && rows.length && rows.every(row => row.first === dash)
      ? 'Винрейт'
      : definition.legend;
  return {
    ...base,
    legend,
    status: rows.length ? 'ok' : 'empty',
    asOf,
    stale,
    message: '',
    rows,
  };
}
