// What a statistics module shows around its table: which filters are visible, the KPI cards and the
// page caption. Pure functions (no React), so tests run as-is.
import { formatDay, formatNumber, formatRelative, knownOptionValues, paramLabel } from './format.ts';
import { maxPage, pageNumber, statsPageSize } from './analytics-query.ts';
import type { AnalyticsData, AnalyticsModule } from './types.ts';

export type Param = AnalyticsModule['params'][string];
export type Kpi = { key: string; label: string; value: string; hint?: string; tone?: string };

/** Paging is the panel's job: these two never become form fields. */
export const pagingParams = ['limit', 'offset'];

export const visibleParams = (definition?: AnalyticsModule): [string, Param][] =>
  Object.entries(definition?.params ?? {}).filter(([key]) => !pagingParams.includes(key));

/** A module can be paged only when the backend takes `offset`. */
export const pagesBackend = (definition?: AnalyticsModule) => Boolean(definition?.params.offset);

/**
 * Values for a list: what the backend offers for this answer, else the registry, else (for a token
 * the panel has wording for, like the meta period) the known values, so a failed answer without
 * `options` still shows a list. The current value is never lost.
 */
export function optionValues(key: string, param: Param, data: AnalyticsData | undefined, current: string) {
  const offered = data?.options?.[key];
  let list = (Array.isArray(offered) ? offered : (param.values ?? [])).map(String);
  if (!list.length && param.type === 'token') list = knownOptionValues(key);
  if (current && list.length && !list.includes(current)) list.unshift(current);
  return list;
}

/** Whether the field is a list (an `enum`, or a token the backend answered with a list for). */
export const isList = (key: string, param: Param, data?: AnalyticsData) =>
  param.type === 'enum' || optionValues(key, param, data, '').length > 0;

/** What a parameter reads when the URL does not set it. */
export const defaultOf = (param: Param) => (param.default === undefined ? '' : String(param.default));

/** True when some visible filter (or the page) differs from the module's own defaults. */
export function isChanged(definition: AnalyticsModule | undefined, query: URLSearchParams) {
  if (pageNumber(query.get('page'), maxPage(definition)) > 1) return true;
  return visibleParams(definition).some(
    ([key, param]) => (query.get(key) ?? defaultOf(param)) !== defaultOf(param),
  );
}

const nouns: Record<string, string> = {
  meta: 'Архетипов',
  archetypes: 'Архетипов',
  hsguru_archetypes: 'Архетипов',
  decks: 'Колод',
  bg_heroes: 'Героев',
  bg_minions: 'Существ',
  arena: 'Классов',
  arena_cards: 'Карт',
  constructed_cards: 'Карт',
  card: 'Карт',
  patches: 'Патчей',
};

const isDate = (item: { key?: string; type?: string }) => item.key === 'data_as_of' || item.type === 'date';

function dateKpi(value: unknown): Kpi | null {
  const day = formatDay(value);
  return day === '—'
    ? null
    : { key: 'data_as_of', label: 'Данные на', value: day, hint: formatRelative(value) };
}

type SummaryItem = { key?: string; label: string; value: unknown; type?: string; tone?: string };

/**
 * KPI cards. The backend `summary` (#104) wins; an old PHP answers `Записей` and filter echoes instead,
 * so then the panel counts itself: the date from `meta.updated_at`, the rows, the sum of games.
 * The selected filters are never repeated, and `Записей` never shows.
 */
export function statsKpis(moduleKey: string, data: AnalyticsData, definition?: AnalyticsModule): Kpi[] {
  const filterLabels = new Set(visibleParams(definition).map(([key]) => paramLabel(key)));
  const own = ((data.summary ?? []) as SummaryItem[]).filter(
    item => item.label !== 'Записей' && !filterLabels.has(item.label),
  );
  if (own.length)
    return own.map(item => {
      const kpi = isDate(item) ? dateKpi(item.value) : null;
      return (
        kpi ?? {
          key: item.key ?? item.label,
          label: item.label,
          value: typeof item.value === 'number' ? formatNumber(item.value) : String(item.value ?? '—'),
          tone: item.tone,
        }
      );
    });
  const total = Number((data.meta as { total?: unknown }).total);
  const count = Number.isFinite(total) && total > 0 ? total : (data.rows?.length ?? 0);
  const games = data.columns?.find(column => /^games/.test(column.key));
  const sum = games ? data.rows.reduce((acc, row) => acc + (Number(row[games.key]) || 0), 0) : 0;
  const date = dateKpi((data.meta as { updated_at?: unknown }).updated_at);
  // A failed or empty answer has nothing to summarise.
  if (!data.rows?.length && !date) return [];
  const kpis: Kpi[] = [];
  if (date) kpis.push(date);
  if (games && sum > 0) kpis.push({ key: 'games', label: 'Игр в выборке', value: formatNumber(sum) });
  kpis.push({ key: 'rows', label: nouns[moduleKey] ?? 'Строк', value: formatNumber(count) });
  return kpis;
}

/** «1–50 из 812» for the page of rows on the screen. */
export function pageCaption(page: number, shown: number, total: number) {
  if (total <= 0 || shown <= 0) return '';
  const from = (page - 1) * statsPageSize + 1;
  return `${formatNumber(from)}–${formatNumber(from + shown - 1)} из ${formatNumber(total)}`;
}
