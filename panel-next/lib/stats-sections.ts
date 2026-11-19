// Where each statistics module lives in the menu: five statistics groups, two monitoring pages, and
// «Прочее» for whatever the backend registry adds later. Pure functions, no React, so tests run as-is.

export type StatsModuleEntry = { key: string; label?: string };
export type StatsGroup = { key: string; label: string; modules: StatsModuleEntry[] };

/** The module that opens for `/?action=analytics` without `stats`. */
export const defaultStatsModule = 'meta';
/** Key of the group collecting registry modules that this map does not know. */
export const otherGroupKey = 'other';

export const statsGroups: StatsGroup[] = [
  {
    key: 'standard',
    label: 'Стандарт и Вольный',
    modules: [
      { key: 'meta', label: 'Мета' },
      { key: 'hsguru_archetypes', label: 'Архетипы HSGuru' },
      { key: 'archetypes', label: 'Архетипы HSReplay' },
      { key: 'decks', label: 'Колоды' },
      { key: 'constructed_cards', label: 'Карты' },
    ],
  },
  {
    key: 'battlegrounds',
    label: 'Поля сражений',
    modules: [
      { key: 'bg_heroes', label: 'Герои' },
      { key: 'bg_minions', label: 'Существа' },
    ],
  },
  {
    key: 'arena',
    label: 'Арена',
    modules: [
      { key: 'arena', label: 'Классы' },
      { key: 'arena_cards', label: 'Карты' },
    ],
  },
  { key: 'cards', label: 'Карты', modules: [{ key: 'card', label: 'Статистика карты' }] },
  { key: 'patches', label: 'Патчи', modules: [{ key: 'patches' }] },
];

/** Operational pages: they sit in the «Мониторинг» menu group next to «Источники», without tabs. */
export const monitoringModules: StatsModuleEntry[] = [
  { key: 'overview', label: 'Реестр источников' },
  { key: 'providers', label: 'Провайдеры' },
];

const grouped = new Map(statsGroups.flatMap(group => group.modules.map(item => [item.key, group] as const)));
const monitoring = new Map(monitoringModules.map(item => [item.key, item]));

export type StatsPlace =
  { section: 'statistics'; group: StatsGroup } | { section: 'monitoring'; label: string };

/** The tabs of one group: registry modules that exist, with the short names of the map. */
export function groupTabs(group: StatsGroup, registry: Record<string, { title: string }>) {
  return group.modules
    .filter(item => registry[item.key])
    .map(item => ({ key: item.key, label: item.label || registry[item.key].title }));
}

/** Registry modules that no group and no monitoring entry claims: they are never dropped, only gathered. */
export function otherModules(registry: Record<string, { title: string }>): StatsModuleEntry[] {
  return Object.entries(registry)
    .filter(([key]) => !grouped.has(key) && !monitoring.has(key))
    .map(([key, module]) => ({ key, label: module.title }));
}

export function otherGroup(registry: Record<string, { title: string }>): StatsGroup | null {
  const modules = otherModules(registry);
  return modules.length ? { key: otherGroupKey, label: 'Прочее', modules } : null;
}

/**
 * Menu place of a module (also what the breadcrumb says). An unknown key with a registry belongs to
 * «Прочее»; without a registry (or when the key is not in it) it is the default module's place.
 */
export function placeOf(moduleKey: string, registry?: Record<string, { title: string }>): StatsPlace {
  const known = grouped.get(moduleKey);
  if (known) return { section: 'statistics', group: known };
  const page = monitoring.get(moduleKey);
  if (page) return { section: 'monitoring', label: page.label || moduleKey };
  const other = registry?.[moduleKey] ? otherGroup(registry) : null;
  if (other) return { section: 'statistics', group: other };
  return placeOf(defaultStatsModule);
}

/** The module a link with this `stats` value opens: a registry key, otherwise the default «Мета». */
export function resolveModule(candidate: string | null, registry: Record<string, unknown>): string {
  if (candidate && registry[candidate]) return candidate;
  if (registry[defaultStatsModule]) return defaultStatsModule;
  return Object.keys(registry)[0] ?? defaultStatsModule;
}

export const statsHref = (key: string) => '/?action=analytics&stats=' + encodeURIComponent(key);

/** Menu id of the group (or monitoring page) that a module highlights. */
export const menuIdOf = (moduleKey: string, registry?: Record<string, { title: string }>): string => {
  const place = placeOf(moduleKey, registry);
  return place.section === 'statistics' ? 'stats:' + place.group.key : 'stats:' + moduleKey;
};

/** What the menu and the breadcrumb call the place of a module: «Статистика · Арена», «Мониторинг». */
export const groupLabelOf = (moduleKey: string, registry?: Record<string, { title: string }>): string => {
  const place = placeOf(moduleKey, registry);
  return place.section === 'statistics' ? 'Статистика · ' + place.group.label : 'Мониторинг';
};
