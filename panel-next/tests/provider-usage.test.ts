import test from 'node:test';
import assert from 'node:assert/strict';
import {
  cheapestProvider,
  formatBytes,
  formatDuration,
  formatUsd,
  reasonLabel,
  windowDays,
} from '../lib/provider-usage.ts';

test('the chart covers every UTC day of the window, quiet days included', () => {
  assert.deepEqual(windowDays('2026-09-29', '2026-10-02'), [
    '2026-09-29',
    '2026-09-30',
    '2026-10-01',
    '2026-10-02',
  ]);
  assert.equal(windowDays('2026-09-21', '2026-10-20').length, 30);
  assert.deepEqual(windowDays('2026-10-02', '2026-10-01'), []);
  assert.deepEqual(windowDays(null, '2026-10-01'), []);
});

test('the cheapest provider is chosen by price per 1000 successful pages', () => {
  const providers = [
    { provider: 'scrape_do', success: 900, usd_per_1k_success: 1.7 },
    { provider: 'decodo', success: 1052, usd_per_1k_success: 1.0 },
    { provider: 'firecrawl', success: 0, usd_per_1k_success: null },
  ];
  assert.equal(cheapestProvider(providers), 'decodo');
  assert.equal(cheapestProvider(providers.slice(1, 2)), null);
  assert.equal(cheapestProvider([]), null);
});

test('usage values read like the provider dashboards', () => {
  assert.equal(formatUsd(1.052), '$1.05');
  assert.equal(formatUsd(0.00123), '$0.0012');
  assert.equal(formatUsd(null), '—');
  assert.equal(formatBytes(5 * 1024 * 1024), '5 МБ');
  assert.equal(formatBytes(512), '512 Б');
  assert.equal(formatDuration(850), '850 мс');
  assert.equal(formatDuration(5120), '5,12 с');
  assert.equal(reasonLabel('target_403'), 'Сайт ответил HTTP 403');
  assert.equal(reasonLabel('http_429'), 'API провайдера: HTTP 429');
  assert.equal(reasonLabel('content_rejected'), 'Не прошёл проверку содержимого');
  assert.equal(reasonLabel('SomethingElse'), 'SomethingElse');
});
