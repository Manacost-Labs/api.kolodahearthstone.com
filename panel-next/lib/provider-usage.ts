import { formatNumber } from './format.ts';
import { record, text } from './model.ts';
import type { Row } from './types.ts';

export const providerLabels: Record<string, string> = {
  decodo: 'Decodo',
  scrape_do: 'Scrape.do',
  firecrawl: 'Firecrawl',
  scrapfly: 'Scrapfly',
};
export const providerOrder = Object.keys(providerLabels);
export const unitLabels: Record<string, string> = { request: 'запрос', credit: 'кредит' };
const reasonLabels: Record<string, string> = {
  content_rejected: 'Не прошёл проверку содержимого',
  blocked_content: 'Блокировка или капча',
  unusable_result: 'Непригодный ответ',
  content_error: 'Пустой или битый ответ',
  timeout: 'Тайм-аут',
  transport: 'Ошибка соединения',
  credit_limit: 'Кончились кредиты',
  oversize: 'Слишком большой ответ',
};
export const num = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;
export const rows = (value: unknown): Row[] => (Array.isArray(value) ? value.map(record) : []);

export function formatBytes(value: unknown): string {
  const bytes = num(value);
  if (bytes === null) return '—';
  const units = ['Б', 'КБ', 'МБ', 'ГБ'];
  let amount = bytes;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? formatNumber(amount) : amount.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} ${units[unit]}`;
}

export function formatUsd(value: unknown): string {
  const amount = num(value);
  if (amount === null) return '—';
  return `$${amount.toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: amount > 0 && amount < 0.1 ? 4 : 2,
  })}`;
}

export function formatDuration(value: unknown): string {
  const ms = num(value);
  if (ms === null) return '—';
  return ms < 1000
    ? `${formatNumber(Math.round(ms))} мс`
    : `${(ms / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с`;
}

export function reasonLabel(reason: string): string {
  if (reasonLabels[reason]) return reasonLabels[reason];
  const http = /^http_(\d{3})$/.exec(reason);
  if (http) return `API провайдера: HTTP ${http[1]}`;
  const target = /^target_(\d{3})$/.exec(reason);
  if (target) return `Сайт ответил HTTP ${target[1]}`;
  return reason;
}

// Every UTC day of the window, so quiet days show as zero like the Decodo chart.
export function windowDays(from: unknown, to: unknown): string[] {
  if (typeof from !== 'string' || typeof to !== 'string') return [];
  const start = Date.parse(`${from}T00:00:00Z`);
  const end = Date.parse(`${to}T00:00:00Z`);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return [];
  const days: string[] = [];
  for (let time = start; time <= end && days.length < 120; time += 86_400_000)
    days.push(new Date(time).toISOString().slice(0, 10));
  return days;
}

export function cheapestProvider(providers: Row[]): string | null {
  let best: Row | null = null;
  for (const item of providers) {
    const price = num(item.usd_per_1k_success);
    if (price === null || !num(item.success)) continue;
    if (best === null || price < (num(best.usd_per_1k_success) ?? Infinity)) best = item;
  }
  return best && providers.length > 1 ? text(best.provider) : null;
}
