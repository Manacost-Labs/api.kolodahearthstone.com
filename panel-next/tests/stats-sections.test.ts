import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  groupLabelOf,
  groupTabs,
  isStatsHome,
  menuIdOf,
  monitoringModules,
  otherGroup,
  placeOf,
  resolveModule,
  statsGroups,
  statsHomeHref,
  statsHomeKey,
  statsHomeMenuId,
  statsHref,
} from '../lib/stats-sections.ts';

const registry: Record<string, { title: string }> = JSON.parse(
  readFileSync(new URL('./fixtures/analytics-registry.json', import.meta.url), 'utf8'),
).modules;

test('five statistics groups in menu order, each module in exactly one place', () => {
  assert.deepEqual(
    statsGroups.map(group => group.label),
    ['Стандарт и Вольный', 'Поля сражений', 'Арена', 'Карты', 'Патчи'],
  );
  const keys = [...statsGroups.flatMap(group => group.modules), ...monitoringModules].map(item => item.key);
  assert.equal(new Set(keys).size, keys.length);
});

test('every one of the 13 registry modules has a place and none falls into «Прочее»', () => {
  assert.equal(Object.keys(registry).length, 13);
  assert.equal(otherGroup(registry), null);
  for (const key of Object.keys(registry)) {
    const place = placeOf(key, registry);
    const listed =
      place.section === 'statistics'
        ? place.group.modules.some(item => item.key === key)
        : monitoringModules.some(item => item.key === key);
    assert.ok(listed, key);
  }
});

test('the tabs of a group carry the short names and only the modules of that group', () => {
  assert.deepEqual(
    groupTabs(statsGroups[0], registry).map(tab => tab.label),
    ['Мета', 'Архетипы HSGuru', 'Архетипы HSReplay', 'Колоды', 'Карты'],
  );
  assert.deepEqual(
    groupTabs(statsGroups[1], registry).map(tab => tab.key),
    ['bg_heroes', 'bg_minions'],
  );
  assert.deepEqual(
    groupTabs(statsGroups[2], registry).map(tab => tab.label),
    ['Классы', 'Карты'],
  );
  // A module without a short name keeps its registry title.
  assert.deepEqual(groupTabs(statsGroups[4], registry), [{ key: 'patches', label: 'Патчи Hearthstone' }]);
  for (const group of statsGroups) for (const tab of groupTabs(group, registry)) assert.ok(registry[tab.key]);
});

test('monitoring pages are not in any statistics group', () => {
  for (const { key } of monitoringModules) {
    assert.equal(placeOf(key, registry).section, 'monitoring');
    assert.equal(menuIdOf(key, registry), 'stats:' + key);
    assert.equal(groupLabelOf(key, registry), 'Мониторинг');
  }
  assert.equal(
    statsGroups.some(group => group.modules.some(item => item.key === 'overview')),
    false,
  );
});

test('a module the map does not know is gathered in «Прочее» instead of being lost', () => {
  const extended = { ...registry, future_stats: { title: 'Новая статистика' } };
  const other = otherGroup(extended);
  assert.ok(other);
  assert.equal(other.label, 'Прочее');
  assert.deepEqual(groupTabs(other, extended), [{ key: 'future_stats', label: 'Новая статистика' }]);
  const place = placeOf('future_stats', extended);
  assert.equal(place.section === 'statistics' && place.group.key, 'other');
  assert.equal(menuIdOf('future_stats', extended), 'stats:other');
  // Known modules keep their group.
  assert.equal(menuIdOf('arena_cards', extended), 'stats:arena');
});

test('a link without `stats` opens the summary; old links and unknown keys still open a module', () => {
  assert.equal(isStatsHome(null), true);
  assert.equal(isStatsHome(undefined), true);
  assert.equal(isStatsHome(''), true);
  assert.equal(isStatsHome(statsHomeKey), true);
  for (const key of [...Object.keys(registry), 'nope']) assert.equal(isStatsHome(key), false, key);
  // Old links keep their module; an unknown key falls back to «Мета» (a useful page, not a blank one).
  assert.equal(resolveModule('nope', registry), 'meta');
  assert.equal(resolveModule(null, registry), 'meta');
  for (const key of Object.keys(registry)) assert.equal(resolveModule(key, registry), key);
  assert.equal(menuIdOf('meta', registry), 'stats:standard');
});

test('the summary has its own address and menu id; no module collides with it', () => {
  assert.equal(statsHomeHref, '/?action=analytics');
  assert.equal(statsHref(statsHomeKey), statsHomeHref);
  assert.equal(statsHref('meta'), '/?action=analytics&stats=meta');
  assert.equal(statsHomeMenuId, 'stats:home');
  assert.equal(statsHomeKey in registry, false);
  const ids = statsGroups.map(group => 'stats:' + group.key);
  assert.equal(ids.includes(statsHomeMenuId), false);
});
