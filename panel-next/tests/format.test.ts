import test from 'node:test';
import assert from 'node:assert/strict';
import {
  classLabel,
  countLabel,
  fieldLabel,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatRelative,
  formatValue,
  inferKind,
  optionLabel,
  paramLabel,
  windowLabel,
  parseDate,
  slugLabel,
  statusMeta,
  toneOf,
  tribeLabels,
  yesNo,
} from '../lib/format.ts';

const now = Date.parse('2026-10-08T12:00:00Z');

test('dates accept ISO, MariaDB UTC and Unix seconds', () => {
  assert.equal(parseDate('2026-10-02 12:00:00')?.toISOString(), '2026-10-02T12:00:00.000Z');
  assert.equal(parseDate('2026-10-02T12:00:00Z')?.toISOString(), '2026-10-02T12:00:00.000Z');
  assert.equal(parseDate(1790000000)?.getUTCFullYear(), 2026);
  for (const broken of ['', 'вчера', null, undefined, {}]) assert.equal(parseDate(broken), null);
  assert.match(formatDateTime('2026-10-02T12:05:00Z'), /^02 окт\.? 2026 г\., 12:05 UTC$/u);
  assert.equal(formatDateTime('not a date'), '—');
});

test('relative times read naturally in Russian', () => {
  assert.equal(formatRelative('2026-10-08T11:59:40Z', now), 'только что');
  assert.equal(formatRelative('2026-10-08T11:55:00Z', now), '5 минут назад');
  assert.equal(formatRelative('2026-10-08T12:42:00Z', now), 'через 42 минуты');
  assert.equal(formatRelative('2026-10-08T09:00:00Z', now), '3 часа назад');
  assert.equal(formatRelative('2026-10-07T12:00:00Z', now), 'вчера');
  assert.match(formatRelative('2026-08-01T12:00:00Z', now), /^01 авг\.? 2026 г\.$/u);
});

test('numbers, percents and counts use Russian grouping', () => {
  assert.match(formatNumber(1234.567), /^1\s234,57$/u);
  assert.equal(formatNumber('0'), '0');
  assert.equal(formatNumber(null), '—');
  assert.equal(formatPercent(52.3), '52,3%');
  assert.equal(formatPercent('12%'), '12%');
  assert.equal(countLabel(21, { one: 'источник', few: 'источника', many: 'источников' }), '21 источник');
  assert.equal(yesNo(1), 'Да');
  assert.equal(yesNo('0'), 'Нет');
});

test('statuses get Russian labels and a tone, unknown ones keep their wording', () => {
  assert.deepEqual(statusMeta('ok'), { label: 'В норме', tone: 'good' });
  assert.deepEqual(statusMeta('CACHED'), { label: 'Из кэша', tone: 'warning' });
  assert.deepEqual(statusMeta('error'), { label: 'Ошибка', tone: 'bad' });
  assert.deepEqual(statusMeta('running'), { label: 'Выполняется', tone: 'info' });
  assert.deepEqual(statusMeta('upstream_failed_hard'), { label: 'upstream_failed_hard', tone: 'bad' });
  assert.deepEqual(statusMeta(''), { label: '—', tone: 'neutral' });
  assert.equal(toneOf('danger'), 'bad');
  assert.equal(toneOf('whatever'), 'neutral');
});

test('slugs and field names become readable labels', () => {
  assert.equal(slugLabel(tribeLabels, 'murloc'), 'Мурлок');
  assert.equal(slugLabel(tribeLabels, 'MECHANICAL'), 'Механизм');
  assert.equal(slugLabel(tribeLabels, 'unknown_tribe'), 'unknown_tribe');
  assert.equal(fieldLabel('tavern_tier'), 'Уровень таверны');
  assert.equal(fieldLabel('hero_power_text'), 'Hero power text');
});

test('field kinds follow the PHP panel heuristics and keep identifiers verbatim', () => {
  const kinds = Object.fromEntries(
    (
      [
        ['card_type', 'minion'],
        ['creature_type', 'murloc'],
        ['state', 'ok'],
        ['in_pool', 1],
        ['art_image', '/uploads/a.jpg'],
        ['source_url', 'https://example.test'],
        ['updated_at', '2026-10-02T12:00:00Z'],
        ['winrate', 51.2],
        ['dbf', 69000],
        ['card_id', 'BG_1'],
        ['health', 3],
        ['id', 7],
      ] as const
    ).map(([key, value]) => [key, inferKind(key, value)]),
  );
  assert.deepEqual(kinds, {
    card_type: 'card_type',
    creature_type: 'creature_type',
    state: 'status',
    in_pool: 'boolean',
    art_image: 'image',
    source_url: 'link',
    updated_at: 'date',
    winrate: 'percent',
    dbf: 'code',
    card_id: 'code',
    health: 'number',
    id: 'text',
  });
  assert.equal(formatValue('code', 69000), '69000');
  assert.equal(formatValue('card_type', 'minion'), 'Существо');
  assert.equal(formatValue('boolean', 0), 'Нет');
});

test('statistics filters use the PHP panel wording', () => {
  assert.equal(paramLabel('card_period'), 'Период карт');
  assert.equal(paramLabel('unknown_param'), 'Unknown param');
  assert.equal(optionLabel('card_period', '7d'), '7 дней');
  assert.equal(optionLabel('rating', '50'), 'Top 50%');
  assert.equal(optionLabel('rank', 'not_listed'), 'not_listed');
});

test('reliability windows read as durations', () => {
  assert.equal(windowLabel('24h'), '24 часа');
  assert.equal(windowLabel('7d'), '7 дней');
  assert.equal(windowLabel('1d'), '1 день');
  assert.equal(windowLabel('custom'), 'custom');
});

test('classes read in Russian from Blizzard slugs and HearthstoneJSON codes', () => {
  assert.equal(classLabel('demon-hunter'), 'Охотник на демонов');
  assert.equal(classLabel('DEMONHUNTER'), 'Охотник на демонов');
  assert.equal(classLabel('mage'), 'Маг');
  assert.equal(classLabel('unknown'), 'unknown');
});
