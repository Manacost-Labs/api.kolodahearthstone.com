import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isChanged,
  isList,
  isServerPaged,
  optionHints,
  optionValues,
  pageCaption,
  statsKpis,
  visibleParams,
} from '../lib/stats-view.ts';
import type { AnalyticsData, AnalyticsModule } from '../lib/types.ts';

const meta: AnalyticsModule = {
  title: 'Мета',
  description: '',
  params: {
    format: { type: 'enum', values: ['standard', 'wild'], default: 'standard' },
    period: { type: 'token', default: 'past_day' },
    limit: { type: 'int', default: 100 },
    offset: { type: 'int', default: 0 },
  },
};
const base = { ok: true, title: 'Мета', description: '', columns: [], rows: [], meta: {} };
const data = (extra: Partial<AnalyticsData>): AnalyticsData =>
  ({ summary: [], ...base, ...extra }) as AnalyticsData;

test('limit and offset are never filter fields', () => {
  assert.deepEqual(
    visibleParams(meta).map(([key]) => key),
    ['format', 'period'],
  );
});

test('the period list comes from the answer and keeps the selected value', () => {
  const answer = data({ options: { period: ['past_day', 'patch_37.0.2'] } });
  assert.deepEqual(optionValues('period', meta.params.period, answer, 'past_day'), [
    'past_day',
    'patch_37.0.2',
  ]);
  assert.deepEqual(optionValues('period', meta.params.period, answer, 'past_week')[0], 'past_week');
  assert.deepEqual(optionValues('format', meta.params.format, answer, 'wild'), ['standard', 'wild']);
  assert.ok(optionValues('period', meta.params.period, undefined, '').includes('past_day'));
});

test('reset shows only when a filter or the page differs from the defaults', () => {
  assert.equal(isChanged(meta, new URLSearchParams('action=analytics&stats=meta')), false);
  assert.equal(isChanged(meta, new URLSearchParams('format=standard')), false);
  assert.equal(isChanged(meta, new URLSearchParams('format=wild')), true);
  assert.equal(isChanged(meta, new URLSearchParams('page=2')), true);
});

test('KPI: the backend summary wins and shows the date as a day, not as ISO text', () => {
  const kpis = statsKpis(
    'meta',
    data({
      summary: [
        { key: 'data_as_of', label: 'Данные на', value: '2026-11-19T11:00:02+00:00', type: 'date' },
        { key: 'games', label: 'Игр в выборке', value: 82865, type: 'number' },
        { key: 'rows', label: 'Архетипов', value: 53, type: 'number' },
      ],
    }),
    meta,
  );
  assert.deepEqual(
    kpis.map(item => [item.label, item.value]),
    [
      ['Данные на', '19 нояб. 2026 г.'],
      ['Игр в выборке', '82 865'],
      ['Архетипов', '53'],
    ],
  );
});

test('KPI: an old PHP answer is counted by the panel; filters and «Записей» never show', () => {
  const kpis = statsKpis(
    'meta',
    data({
      summary: [
        { label: 'Формат', value: 'standard' },
        { label: 'Период', value: 'past_day' },
        { label: 'Записей', value: 2 },
      ],
      columns: [{ key: 'games', label: 'Игры' }],
      rows: [{ games: 1000 }, { games: 234 }],
      meta: { total: 2, updated_at: '2026-11-19T11:00:02Z' },
    }),
    meta,
  );
  assert.deepEqual(
    kpis.map(item => [item.label, item.value]),
    [
      ['Данные на', '19 нояб. 2026 г.'],
      ['Игр в выборке', '1 234'],
      ['Архетипов', '2'],
    ],
  );
});

test('KPI: a failed answer has nothing to summarise', () => {
  assert.deepEqual(statsKpis('meta', data({}), meta), []);
});

test('the page caption counts rows from the page offset', () => {
  assert.equal(pageCaption(1, 25, 812), '1–25 из 812');
  assert.equal(pageCaption(3, 25, 2777), '51–75 из 2 777');
  assert.equal(pageCaption(1, 0, 0), '');
});

test('the period is a list even when the answer has no options (a failed load)', () => {
  const failed = data({});
  const values = optionValues('period', meta.params.period, failed, 'past_day');
  assert.ok(values.length > 3 && values.includes('past_day') && values.includes('past_week'));
  assert.ok(
    values.every(value => value.startsWith('past_')),
    String(values),
  );
  // A patch from the address is kept and listed first.
  assert.equal(optionValues('period', meta.params.period, failed, 'patch_37.0.2')[0], 'patch_37.0.2');
  // A token without wording stays a typed field.
  assert.deepEqual(optionValues('source_id', { type: 'token' }, failed, ''), []);
});

test('class and format strings are lists with «Все» first; the source stays a field', () => {
  const decks = {
    class_name: { type: 'string' },
    format_name: { type: 'string' },
    source_id: { type: 'token' },
  };
  assert.equal(isList('class_name', decks.class_name, data({})), true);
  assert.equal(optionValues('class_name', decks.class_name, data({}), '')[0], '');
  assert.deepEqual(optionValues('format_name', decks.format_name, data({}), ''), ['', 'standard', 'wild']);
  assert.equal(isList('source_id', decks.source_id, data({})), false);
  assert.equal(isList('q', { type: 'string' }, data({})), false);
});

test('period hints name the current patch and the default', () => {
  const answer = data({
    option_hints: { period: { default: 'past_day', current: 'black_empire', current_patch: 'patch_37.0.2' } },
  });
  assert.deepEqual(optionHints('period', answer), {
    past_day: 'По умолчанию',
    'patch_37.0.2': 'Текущий патч',
  });
  assert.deepEqual(optionHints('period', data({})), {});
  assert.deepEqual(optionHints('format', answer), {});
});

const gamesKpis = [
  { key: 'data_as_of', label: 'Данные на', value: '2026-11-19T11:00:02+00:00', type: 'date' },
  { key: 'games', label: 'Игр в выборке', value: 100, type: 'number' },
  { key: 'rows', label: 'Архетипов', value: 25, type: 'number' },
  { key: 'with_decks', label: 'Со сборками', value: 25, type: 'number' },
];

test('KPI on a partial answer: the real count, no sums over the shown rows', () => {
  const rows = Array.from({ length: 25 }, () => ({ games: 4 }));
  const partial = statsKpis(
    'hsguru_archetypes',
    data({ summary: gamesKpis, rows, meta: { total: 242 } }),
    meta,
  );
  assert.deepEqual(
    partial.map(item => [item.label, item.value]),
    [
      ['Данные на', '19 нояб. 2026 г.'],
      ['Архетипов', '242'],
    ],
  );
  // The same answer when everything came back: nothing is dropped.
  const whole = statsKpis('hsguru_archetypes', data({ summary: gamesKpis, rows, meta: { total: 25 } }), meta);
  assert.deepEqual(
    whole.map(item => item.label),
    ['Данные на', 'Игр в выборке', 'Архетипов', 'Со сборками'],
  );
  // Unknown total: the rows are all there is.
  assert.equal(statsKpis('hsguru_archetypes', data({ summary: gamesKpis, rows, meta: {} }), meta).length, 4);
});

test('KPI on a partial answer from an old PHP: count from meta.total, no games sum', () => {
  const rows = Array.from({ length: 25 }, () => ({ games: 4 }));
  const kpis = statsKpis(
    'decks',
    data({
      columns: [{ key: 'games', label: 'Игры' }],
      rows,
      meta: { total: 2777, updated_at: '2026-11-19T11:00:02Z' },
    }),
    meta,
  );
  assert.deepEqual(
    kpis.map(item => [item.label, item.value]),
    [
      ['Данные на', '19 нояб. 2026 г.'],
      ['Колод', '2\u00a0777'],
    ],
  );
});

test('the patches summary loses its «Показано» counter on a partial answer, «Всего» stays', () => {
  const rows = Array.from({ length: 25 }, () => ({}));
  const kpis = statsKpis(
    'patches',
    data({
      summary: [
        { label: 'Показано', value: 25 },
        { label: 'Всего', value: 322 },
        { label: 'Сопоставлено', value: 200 },
      ],
      rows,
      meta: { total: 322 },
    }),
    meta,
  );
  assert.deepEqual(
    kpis.map(item => item.label),
    ['Всего', 'Сопоставлено'],
  );
});

test('only decks and patches page on the server', () => {
  const noOffset = { ...meta, params: { format: meta.params.format } };
  assert.equal(isServerPaged('decks', meta), true);
  assert.equal(isServerPaged('bg_minions', meta), false);
  assert.equal(isServerPaged('decks', noOffset), false);
});

test('a small bounded int without a default is a list; paging and defaulted ints stay fields', () => {
  const tier = { type: 'int', min: 1, max: 7 };
  assert.equal(isList('tavern_tier', tier, data({})), true);
  assert.deepEqual(optionValues('tavern_tier', tier, data({}), ''), ['', '1', '2', '3', '4', '5', '6', '7']);
  assert.equal(isList('limit', { type: 'int', default: 500, min: 1, max: 500 }, data({})), false);
  assert.equal(isList('offset', { type: 'int', default: 0, min: 0, max: 10000 }, data({})), false);
  assert.equal(isList('min_games', { type: 'int', default: 50, min: 50, max: 1000000 }, data({})), false);
  assert.equal(isList('level', { type: 'int', min: 1, max: 500 }, data({})), false);
  assert.equal(isList('level', { type: 'int' }, data({})), false);
});
