'use client';
import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { countLabel, fieldLabel, formatNumber, optionLabel, paramLabel, toneOf } from '@/lib/format';
import { queryHref, text } from '@/lib/model';
import type { AnalyticsData, AnalyticsModule, Row } from '@/lib/types';
import { DataTable } from './DataTable';
import { RecordDetails } from './Details';
import { Reliability } from './Reliability';
import { EmptyState, KpiCard, StatusBadge, Time, Value } from './ui';
export function Analytics({
  data,
  modules,
  module,
  query,
}: {
  data: AnalyticsData;
  modules: Record<string, AnalyticsModule>;
  module: string;
  query: string;
}) {
  const router = useRouter();
  const [pending, start] = useTransition();
  const [selected, setSelected] = useState<Row | null>(null);
  const params = new URLSearchParams(query);
  const definition = modules[module];
  const go = (changes: Record<string, string | number | null>) =>
    start(() => router.push(queryHref(query, changes)));
  const extras = Object.entries(data).filter(
    ([key]) => !['ok', 'module', 'title', 'description', 'summary', 'columns', 'rows', 'meta'].includes(key),
  );
  return (
    <section aria-busy={pending}>
      <header className="page-head">
        <div>
          <span className="eyebrow">Аналитика</span>
          <h1>{data.title}</h1>
          <p>{data.description}</p>
        </div>
        <button className="button secondary" type="button" onClick={() => start(() => router.refresh())}>
          Обновить
        </button>
      </header>
      <div className="analytics-layout">
        <nav className="module-nav" aria-label="Наборы статистики">
          {Object.entries(modules).map(([key, item]) => (
            <button
              key={key}
              type="button"
              aria-current={key === module ? 'page' : undefined}
              onClick={() => start(() => router.push('/?action=analytics&stats=' + key))}
            >
              {item.title}
            </button>
          ))}
        </nav>
        <div className="analytics-main">
          <form
            className="filter-row"
            onSubmit={e => {
              e.preventDefault();
              const form = new FormData(e.currentTarget);
              go(Object.fromEntries([...form.entries()].map(([key, v]) => [key, String(v)])));
            }}
          >
            {Object.entries(definition?.params || {}).map(([key, param]) =>
              param.type === 'enum' ? (
                <label key={key}>
                  <span>{paramLabel(key)}</span>
                  <select
                    name={key}
                    defaultValue={params.get(key) || String(param.default || '')}
                    onChange={e => go({ [key]: e.target.value })}
                  >
                    {(param.values || []).map(value => (
                      <option key={value} value={value}>
                        {optionLabel(key, value)}
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                <label key={key}>
                  <span>{paramLabel(key)}</span>
                  <input
                    name={key}
                    type={param.type === 'int' ? 'number' : 'search'}
                    min={param.min}
                    max={param.max}
                    defaultValue={params.get(key) || text(param.default, '')}
                  />
                </label>
              ),
            )}
            {!!Object.keys(definition?.params || {}).length && (
              <button className="button" type="submit">
                Применить
              </button>
            )}
          </form>
          <div className="summary-grid">
            {(data.summary || []).map(item => (
              <KpiCard
                key={item.label}
                label={item.label}
                value={typeof item.value === 'number' ? formatNumber(item.value) : text(item.value)}
                tone={toneOf(item.tone)}
              />
            ))}
          </div>
          <div className="data-status">
            {data.meta?.stale || data.meta?.stale_cache ? (
              <StatusBadge tone="warning" label="Резервные данные" />
            ) : (
              <StatusBadge tone="good" label="Актуальный набор" />
            )}
            <span>
              Обновлено: <Time value={data.meta?.updated_at} />
            </span>
            {Boolean(data.meta?.cached) && (
              <span>
                Из кэша ·{' '}
                {countLabel(Number(data.meta?.cache_age) || 0, {
                  one: 'секунда',
                  few: 'секунды',
                  many: 'секунд',
                })}
              </span>
            )}
          </div>
          {data.rows?.length ? (
            <DataTable key={module} rows={data.rows} columns={data.columns} onDetail={setSelected} />
          ) : (
            <EmptyState title="По этим условиям данных нет">
              Измените фильтры или выберите другой набор.
            </EmptyState>
          )}
          {extras.map(([key, value]) =>
            key === 'parsing_reliability' ? (
              <Reliability key={key} data={value} />
            ) : (
              <details key={key} className="extra-data">
                <summary>{fieldLabel(key)}</summary>
                <Value value={value} name={key} />
              </details>
            ),
          )}
        </div>
      </div>
      {selected && (
        <RecordDetails
          title={text(
            selected.name_ru || selected.name || selected.label || selected.source_id,
            'Детали записи',
          )}
          row={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </section>
  );
}
