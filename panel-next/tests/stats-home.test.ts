import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  buildCard,
  dataDate,
  homeCards,
  readNumber,
  staleAfterMs,
  topRows,
  type HomeCardDefinition,
} from '../lib/stats-home.ts';
import { analyticsRequest } from '../lib/analytics-query.ts';
import type { AnalyticsData } from '../lib/types.ts';

const registry = JSON.parse(
  readFileSync(new URL('./fixtures/analytics-registry.json', import.meta.url), 'utf8'),
).modules;
const card = (key: string) => homeCards.find(item => item.key === key) as HomeCardDefinition;
const answer = (rows: Record<string, unknown>[], extra: Partial<AnalyticsData> = {}): AnalyticsData => ({
  ok: true,
  title: 't',
  description: 'd',
  columns: [],
  rows,
  summary: [],
  meta: {},
  ...extra,
});
const asOf = '2026-11-19T11:00:02+00:00';
const now = Date.parse('2026-11-19T12:00:00Z');

test('four cards in the order of the screen, each reading an existing module with a link into its mode', () => {
  assert.deepEqual(
    homeCards.map(item => [item.key, item.module, item.href]),
    [
      ['standard', 'meta', '/?action=analytics&stats=meta&format=standard'],
      ['wild', 'meta', '/?action=analytics&stats=meta&format=wild'],
      ['bg', 'bg_heroes', '/?action=analytics&stats=bg_heroes'],
      ['arena', 'arena', '/?action=analytics&stats=arena'],
    ],
  );
  for (const item of homeCards) {
    assert.ok(registry[item.module], item.module);
    // Every filter of a card is a filter the module really has, with a value it allows.
    for (const [name, value] of Object.entries(item.params)) {
      const rule = registry[item.module].params[name];
      assert.ok(rule, `${item.module}.${name}`);
      if (rule.values) assert.ok(rule.values.includes(value), `${item.module}.${name}=${value}`);
    }
  }
});

test('the request of a card is the module request of the statistics page (format, mode, the largest limit)', () => {
  const ask = (key: string) => {
    const item = card(key);
    return analyticsRequest(new URLSearchParams(item.params), item.module, registry[item.module]).backend;
  };
  assert.equal(ask('standard').toString(), 'format=standard&module=meta');
  assert.equal(ask('wild').toString(), 'format=wild&module=meta');
  assert.equal(ask('bg').get('mode'), 'solo');
  assert.equal(ask('bg').get('limit'), '200');
  assert.equal(ask('bg').has('offset'), false);
  assert.equal(ask('arena').get('limit'), '100');
});

test('readNumber reads numbers and percent strings, and never turns a missing value into 0', () => {
  assert.equal(readNumber(52.3), 52.3);
  assert.equal(readNumber('7.98%'), 7.98);
  assert.equal(readNumber('58,5 %'), 58.5);
  for (const bad of [null, undefined, '', 'n/a', NaN, {}, '%']) assert.equal(readNumber(bad), null);
});

test('data date: summary data_as_of first, then meta.updated_at, otherwise none', () => {
  const summary = [{ key: 'data_as_of', label: 'Данные на', value: asOf }];
  assert.equal(
    dataDate(answer([], { summary, meta: { updated_at: '2026-11-01T00:00:00Z' } })),
    '2026-11-19T11:00:02.000Z',
  );
  assert.equal(
    dataDate(answer([], { meta: { updated_at: '2026-11-18T10:00:00Z' } })),
    '2026-11-18T10:00:00.000Z',
  );
  assert.equal(dataDate(answer([], { summary: [{ key: 'data_as_of', label: 'x', value: 'мусор' }] })), null);
  assert.equal(dataDate(answer([])), null);
  assert.equal(dataDate(null), null);
});

test('Standard/Wild: top three by share with icon, Russian name, share and win rate', () => {
  const rows = [
    { archetype: 'Aggro Hunter', class_name: 'Hunter', popularity: 5, winrate: 49.5 },
    { archetype: 'Plugh Xyzzy', class_name: 'Warlock', popularity: 12.25, winrate: '54.1%' },
    { archetype: 'Tempo Mage', class_name: 'Mage', popularity: '7.98%', winrate: 51 },
    { archetype: 'Combo Rogue', class_name: 'Rogue', popularity: 3, winrate: 60 },
  ];
  const top = topRows('standard', rows);
  assert.deepEqual(
    top.map(row => [row.rank, row.title, row.classSlug, row.first, row.second, row.up]),
    [
      [1, 'Plugh Xyzzy', 'warlock', '12,3 %', '54,1 %', true],
      [2, 'Tempo Mage', 'mage', '8,0 %', '51,0 %', true],
      [3, 'Aggro Hunter', 'hunter', '5,0 %', '49,5 %', false],
    ],
  );
  assert.ok(top.every(row => row.icon));
});

test('hsguru-style rows (popularity_pct) work too, and ties keep the answer order', () => {
  const top = topRows('wild', [
    { archetype: 'A Druid', popularity_pct: 4, winrate: 50 },
    { archetype: 'B Druid', popularity_pct: 4, winrate: 51 },
  ]);
  assert.deepEqual(
    top.map(row => [row.first, row.second]),
    [
      ['4,0\u00a0%', '50,0\u00a0%'],
      ['4,0\u00a0%', '51,0\u00a0%'],
    ],
  );
  assert.equal(top[0].rank, 1);
});

test('a row with a missing number stays with «—»; a row with no numbers at all is dropped; 1–2 rows show as they are', () => {
  const top = topRows('standard', [
    { archetype: 'Aggro Hunter', class_name: 'Hunter', popularity: 5 },
    { archetype: 'Empty Mage', class_name: 'Mage' },
    { archetype: 'Bad Rogue', class_name: 'Rogue', popularity: 'n/a', winrate: null },
  ]);
  assert.equal(top.length, 1);
  assert.deepEqual([top[0].first, top[0].second, top[0].up], ['5,0 %', '—', false]);
});

test('Battlegrounds: heroes by pick rate with average placement, no class icon, no invented win rate', () => {
  const top = topRows('bg', [
    { hero: 'Hero B', pick_rate_value: 40, avg_placement: 4.1, first_place: 99 },
    { hero: 'Hero A', pick_rate_value: 59.35, avg_placement: 3.98 },
    { name: 'Hero C', pick_rate_value: 10, avg_placement: null },
    { hero: 'Hero D', pick_rate_value: 5, avg_placement: 4.5 },
    { pick_rate_value: 70, avg_placement: 1 },
  ]);
  assert.deepEqual(
    top.map(row => [row.title, row.first, row.second, row.icon, row.classSlug, row.up]),
    [
      ['Hero A', '59,4 %', '3,98', false, null, false],
      ['Hero B', '40,0 %', '4,10', false, null, false],
      ['Hero C', '10,0 %', '—', false, null, false],
    ],
  );
});

test('Arena: classes by pick rate with the win rate; without a pick rate only the win rate', () => {
  const rows = [
    { class_name: 'Hunter', pick_rate: 47.29, win_rate: 51.69 },
    { class_name: 'Death Knight', pick_rate: 83.14, win_rate: 52.52 },
    { class_name: 'Mage', pick_rate: 20, win_rate: 48 },
    { class_name: 'Priest', pick_rate: 10, win_rate: 49 },
  ];
  const top = topRows('arena', rows);
  assert.deepEqual(
    top.map(row => [row.title, row.classSlug, row.first, row.second]),
    [
      ['Рыцарь смерти', 'deathknight', '83,1 %', '52,5 %'],
      ['Охотник', 'hunter', '47,3 %', '51,7 %'],
      ['Маг', 'mage', '20,0 %', '48,0 %'],
    ],
  );
  const onlyWins = buildCard(card('arena'), {
    data: answer([
      { class_name: 'Hunter', win_rate: 51 },
      { class_name: 'Mage', win_rate: 53 },
    ]),
    message: '',
  });
  assert.equal(onlyWins.legend, 'Винрейт');
  assert.deepEqual(
    onlyWins.rows.map(row => row.title),
    ['Маг', 'Охотник'],
  );
});

test('a card with data: status, date, no stale flag for fresh data', () => {
  const built = buildCard(
    card('standard'),
    {
      data: answer([{ archetype: 'Aggro Hunter', class_name: 'Hunter', popularity: 5, winrate: 50 }], {
        summary: [{ key: 'data_as_of', label: 'Данные на', value: asOf }],
      }),
      message: '',
    },
    now,
  );
  assert.equal(built.status, 'ok');
  assert.equal(built.asOf, '2026-11-19T11:00:02.000Z');
  assert.equal(built.stale, false);
  assert.equal(built.href, '/?action=analytics&stats=meta&format=standard');
});

test('data older than three days is marked stale; exactly three days is not', () => {
  const at = (ms: number) =>
    buildCard(
      card('arena'),
      {
        data: answer([{ class_name: 'Mage', win_rate: 50 }], {
          meta: { updated_at: new Date(now - ms).toISOString() },
        }),
        message: '',
      },
      now,
    ).stale;
  assert.equal(at(staleAfterMs), false);
  assert.equal(at(staleAfterMs + 1000), true);
});

test('a failed module becomes an error card with its message and keeps its link; the others are untouched', () => {
  const failed = buildCard(card('bg'), { data: null, message: 'Не удалось загрузить статистику.' });
  assert.equal(failed.status, 'error');
  assert.equal(failed.message, 'Не удалось загрузить статистику.');
  assert.equal(failed.href, '/?action=analytics&stats=bg_heroes');
  assert.deepEqual([failed.rows, failed.asOf], [[], null]);
  const fine = buildCard(
    card('arena'),
    { data: answer([{ class_name: 'Mage', win_rate: 50, pick_rate: 5 }]), message: '' },
    now,
  );
  assert.equal(fine.status, 'ok');
});

test('an answer without usable rows is an empty card, still with its date', () => {
  const empty = buildCard(
    card('standard'),
    { data: answer([], { meta: { updated_at: asOf } }), message: '' },
    now,
  );
  assert.equal(empty.status, 'empty');
  assert.equal(empty.asOf, '2026-11-19T11:00:02.000Z');
  const junk = buildCard(card('standard'), { data: answer([{ archetype: 'X Mage' }]), message: '' }, now);
  assert.equal(junk.status, 'empty');
  assert.equal(
    buildCard(card('standard'), { data: answer(undefined as never), message: '' }).status,
    'empty',
  );
});
