'use client';
import { useState } from 'react';
import { formatNumber, formatPercent } from '@/lib/format';
import { record, text } from '@/lib/model';
import type { Row } from '@/lib/types';
import {
  cheapestProvider,
  formatBytes,
  formatDuration,
  formatUsd,
  num,
  providerLabels,
  providerOrder,
  reasonLabel,
  rows,
  unitLabels,
  windowDays,
} from '@/lib/provider-usage';
import { StatusBadge, Time } from './ui';
import { SegmentedControl } from './varylo-ui/navigation/SegmentedControl';

type Metric = 'total' | 'success_rate' | 'avg_ms' | 'bytes' | 'cost_usd';

const metrics: { value: Metric; label: string }[] = [
  { value: 'total', label: 'Запросы' },
  { value: 'success_rate', label: 'Успешность' },
  { value: 'avg_ms', label: 'Время ответа' },
  { value: 'bytes', label: 'Трафик' },
  { value: 'cost_usd', label: 'Расход' },
];

function formatMetric(metric: Metric, value: unknown): string {
  if (metric === 'success_rate') return formatPercent(value);
  if (metric === 'avg_ms') return formatDuration(value);
  if (metric === 'bytes') return formatBytes(value);
  if (metric === 'cost_usd') return formatUsd(value);
  return formatNumber(value);
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].find(factor => factor * magnitude >= value) ?? 10;
  return step * magnitude;
}

function Chart({ days, daily, providers }: { days: string[]; daily: Row[]; providers: string[] }) {
  const [metric, setMetric] = useState<Metric>('total');
  const [hover, setHover] = useState<number | null>(null);
  const width = 960;
  const height = 280;
  const pad = { top: 16, right: 16, bottom: 44, left: 56 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const byKey = new Map(daily.map(item => [`${text(item.day)}|${text(item.provider)}`, item]));
  const value = (day: string, provider: string): number | null => {
    const item = byKey.get(`${day}|${provider}`);
    if (!item) return metric === 'success_rate' || metric === 'avg_ms' ? null : 0;
    return num(item[metric]);
  };
  const values = days.flatMap(day => providers.map(provider => value(day, provider) ?? 0));
  const max = metric === 'success_rate' ? 100 : niceMax(Math.max(0, ...values));
  const x = (index: number) =>
    pad.left + (days.length > 1 ? (index * plotWidth) / (days.length - 1) : plotWidth / 2);
  const y = (amount: number) => pad.top + plotHeight - (amount / max) * plotHeight;
  const labelEvery = Math.max(1, Math.ceil(days.length / 10));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map(fraction => fraction * max);
  const pick = (clientX: number, box: DOMRect) => {
    const ratio = ((clientX - box.left) / box.width) * width;
    const index = Math.round(((ratio - pad.left) / plotWidth) * (days.length - 1));
    setHover(Math.min(days.length - 1, Math.max(0, index)));
  };
  return (
    <div className="provider-chart">
      <div className="data-status">
        <h2>По дням</h2>
        <SegmentedControl
          aria-label="Показатель графика"
          size="sm"
          value={metric}
          onValueChange={next => setMetric(next as Metric)}
          options={metrics}
        />
      </div>
      <div className="provider-chart-frame">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`${metrics.find(item => item.value === metric)?.label} по дням и провайдерам`}
          onPointerMove={event => pick(event.clientX, event.currentTarget.getBoundingClientRect())}
          onPointerLeave={() => setHover(null)}
        >
          {ticks.map(tick => (
            <g key={tick}>
              <line className="grid" x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} />
              <text className="axis" x={pad.left - 8} y={y(tick) + 4} textAnchor="end">
                {formatMetric(metric, tick)}
              </text>
            </g>
          ))}
          {days.map((day, index) =>
            index % labelEvery === 0 || index === days.length - 1 ? (
              <text key={day} className="axis" x={x(index)} y={height - 16} textAnchor="middle">
                {day.slice(5).replace('-', '.')}
              </text>
            ) : null,
          )}
          {providers.map(provider => {
            const points = days
              .map((day, index) => {
                const amount = value(day, provider);
                return amount === null ? null : `${x(index)},${y(amount)}`;
              })
              .filter(Boolean)
              .join(' ');
            return (
              <polyline
                key={provider}
                className="series"
                data-provider={provider}
                points={points}
                fill="none"
              />
            );
          })}
          {hover !== null && (
            <line className="cursor" x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + plotHeight} />
          )}
        </svg>
        {hover !== null && days[hover] && (
          <div
            className="provider-tooltip"
            style={{ left: `${(x(hover) / width) * 100}%` }}
            data-side={hover > days.length / 2 ? 'left' : 'right'}
          >
            <strong>{days[hover]}</strong>
            {providers.map(provider => (
              <span key={provider}>
                <i data-provider={provider} aria-hidden="true" />
                {providerLabels[provider]}
                <b>{formatMetric(metric, value(days[hover], provider))}</b>
              </span>
            ))}
          </div>
        )}
      </div>
      <ul className="provider-legend">
        {providers.map(provider => (
          <li key={provider}>
            <i data-provider={provider} aria-hidden="true" />
            {providerLabels[provider]}
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ProviderUsage({ data }: { data: unknown }) {
  const usage = record(data);
  const providers = rows(usage.providers).sort(
    (a, b) => providerOrder.indexOf(text(a.provider)) - providerOrder.indexOf(text(b.provider)),
  );
  const daily = rows(usage.daily);
  const days = windowDays(usage.from, usage.to);
  const cheapest = cheapestProvider(providers);
  const pricing = record(usage.pricing);
  const reasons = rows(usage.failure_reasons);
  const sources = rows(usage.sources).filter(item => num(item.total));
  const error = typeof usage.error === 'string' ? usage.error : '';
  return (
    <section className="provider-usage" aria-label="Учёт провайдеров">
      {error && (
        <p role="alert" className="notice bad">
          Учёт провайдеров недоступен: {error}
        </p>
      )}
      {providers.length > 0 && (
        <div className="provider-cards">
          {providers.map(item => {
            const key = text(item.provider);
            const price = record(pricing[key]);
            return (
              <article key={key} data-provider={key}>
                <header>
                  <i data-provider={key} aria-hidden="true" />
                  <h3>{providerLabels[key] ?? key}</h3>
                  {cheapest === key && <StatusBadge tone="good" label="Дешевле всех" />}
                </header>
                <strong>{formatUsd(item.usd_per_1k_success)}</strong>
                <span className="muted">за 1000 успешных</span>
                <dl>
                  <dt>Успешность</dt>
                  <dd>{formatPercent(item.success_rate)}</dd>
                  <dt>Запросов</dt>
                  <dd>
                    {formatNumber(item.total)} · <span className="good">{formatNumber(item.success)}</span> /{' '}
                    <span className="bad">{formatNumber(item.failed)}</span>
                  </dd>
                  <dt>Время ответа</dt>
                  <dd>
                    {formatDuration(item.avg_ms)} · p95 {formatDuration(item.p95_ms)}
                  </dd>
                  <dt>Трафик</dt>
                  <dd>{formatBytes(item.bytes)}</dd>
                  <dt>Расход</dt>
                  <dd>
                    {formatUsd(item.cost_usd)} · {formatNumber(item.cost_units)}{' '}
                    {unitLabels[text(item.cost_unit)] ?? text(item.cost_unit)}
                  </dd>
                  <dt>Цена единицы</dt>
                  <dd>
                    {formatUsd(price.usd_per_unit)}
                    {price.basis === 'configured' ? '' : ' · по прайсу'}
                  </dd>
                </dl>
              </article>
            );
          })}
        </div>
      )}
      {days.length > 0 && providers.length > 0 && (
        <Chart days={days} daily={daily} providers={providers.map(item => text(item.provider))} />
      )}
      <div className="data-status">
        <span>Сутки в UTC</span>
        {Boolean(usage.first_recorded_at) && (
          <span>
            Учёт ведётся с <Time value={usage.first_recorded_at} />
          </span>
        )}
        {Boolean(usage.retention_days) && <span>Хранение {text(usage.retention_days)} дней</span>}
        <span>Успех — ответ 2xx, прошедший проверку содержимого</span>
      </div>
      {(reasons.length > 0 || sources.length > 0) && (
        <div className="provider-breakdowns">
          {reasons.length > 0 && (
            <details className="extra-data" open>
              <summary>Причины неудач</summary>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Провайдер</th>
                    <th scope="col">Причина</th>
                    <th scope="col">Вызовов</th>
                  </tr>
                </thead>
                <tbody>
                  {reasons.map(item => (
                    <tr key={`${text(item.provider)}|${text(item.reason)}`}>
                      <td>{providerLabels[text(item.provider)]}</td>
                      <td title={text(item.reason)}>{reasonLabel(text(item.reason))}</td>
                      <td>{formatNumber(item.count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
          {sources.length > 0 && (
            <details className="extra-data">
              <summary>Источники по расходу</summary>
              <table>
                <thead>
                  <tr>
                    <th scope="col">Источник</th>
                    <th scope="col">Вызовов</th>
                    <th scope="col">Успешность</th>
                    <th scope="col">Расход</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map(item => (
                    <tr key={text(item.source_id, '—')}>
                      <td>
                        <code>{text(item.source_id, 'без источника')}</code>
                      </td>
                      <td>{formatNumber(item.total)}</td>
                      <td>{formatPercent(item.success_rate)}</td>
                      <td>{formatUsd(item.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}
    </section>
  );
}
