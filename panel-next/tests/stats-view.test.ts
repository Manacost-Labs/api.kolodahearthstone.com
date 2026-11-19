import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isChanged,
  isList,
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
      meta: { total: 53, updated_at: '2026-11-19T11:00:02Z' },
    }),
    meta,
  );
  assert.deepEqual(
    kpis.map(item => [item.label, item.value]),
    [
      ['Данные на', '19 нояб. 2026 г.'],
      ['Игр в выборке', '1 234'],
      ['Архетипов', '53'],
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
