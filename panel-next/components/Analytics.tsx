'use client';
import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { countLabel, fieldLabel, formatNumber, toneOf } from '@/lib/format';
import { maxPage, pageNumber, statsPageSize } from '@/lib/analytics-query';
import { isChanged, pageCaption, pagesBackend, statsKpis, visibleParams } from '@/lib/stats-view';
import { statsHref } from '@/lib/stats-sections';
import { queryHref, text } from '@/lib/model';
import type { AnalyticsData, AnalyticsModule, Row } from '@/lib/types';
import { DataTable } from './DataTable';
import { RecordDetails } from './Details';
import { ProviderUsage } from './ProviderUsage';
import { Reliability } from './Reliability';
import { EmptyState, KpiCard, StatusBadge, Time, Value } from './ui';
import { ArrowClockwiseIcon } from '@phosphor-icons/react/dist/ssr/ArrowClockwise';
import { StatsModuleTabs } from './StatsModuleTabs';
import { Button } from './varylo-ui/basic/Button';
import { ModuleView } from './stats/ModuleView';
import { StatsFilters } from './stats/StatsFilters';
import { StatsPager } from './stats/StatsPager';
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
  // A required filter without a value: the module has nothing to show yet, only the field.
  const missing = visibleParams(definition).some(
    ([key, param]) => param.required && !(params.get(key) || '').trim(),
  );
  const kpis = statsKpis(module, data, definition);
  const pageNo = pageNumber(params.get('page'), maxPage(definition));
  const total = Number(data.meta?.total) || data.rows?.length || 0;
  // The last page the data (and the backend's `offset` limit) reaches; a page past it has no rows.
  const lastPage = Math.max(1, Math.min(Math.ceil(total / statsPageSize), maxPage(definition)));
  const beyond = pagesBackend(definition) && pageNo > 1 && total > 0 && !data.rows?.length;
  const extras = Object.entries(data).filter(
    ([key]) =>
      ![
        'ok',
        'module',
        'title',
        'description',
        'summary',
        'columns',
        'rows',
        'meta',
        'provider_usage',
        // The filters already use them (lists and the hints beside the period).
        'options',
        'option_hints',
      ].includes(key),
  );
  return (
    <section aria-busy={pending}>
      <header className="page-head">
        <div>
          <h1>{data.title}</h1>
          <p>{data.description}</p>
        </div>
        <button className="button secondary" type="button" onClick={() => start(() => router.refresh())}>
          <ArrowClockwiseIcon size={16} aria-hidden="true" /> Обновить
        </button>
      </header>
      <div className="analytics-layout">
        <StatsModuleTabs
          modules={modules}
          module={module}
          onSelect={key => start(() => router.push('/?action=analytics&stats=' + key))}
        />
        <div className="analytics-main" data-module={module}>
          <StatsFilters
            definition={definition}
            data={data}
            query={params}
            changed={isChanged(definition, params)}
            onChange={go}
            onReset={() => start(() => router.push(statsHref(module)))}
          />
          {missing ? (
            <EmptyState title="Выберите карту">
              Введите название карты в поле выше и нажмите Enter — покажем её винрейт, колоды и динамику.
            </EmptyState>
          ) : (
            <div className="stats-body" data-busy={pending}>
              <div className="summary-grid stats-kpis">
                {kpis.map(item => (
                  <KpiCard
                    key={item.key}
                    label={item.label}
                    value={item.value}
                    hint={item.hint}
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
                {/* «Данные на» in the KPI row already says when; repeat it only when no KPI does. */}
                {Boolean(data.meta?.updated_at) && !kpis.some(item => item.key === 'data_as_of') && (
                  <span>
                    Обновлено: <Time value={data.meta?.updated_at} />
                  </span>
                )}
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
              <ModuleView module={module} data={data} />
              {'provider_usage' in data && <ProviderUsage data={data.provider_usage} />}
              {beyond ? (
                <EmptyState
                  title="Такой страницы нет"
                  action={
                    <Button
                      variant="secondary"
                      onClick={() => go({ page: lastPage === 1 ? null : lastPage })}
                    >
                      Перейти на последнюю страницу
                    </Button>
                  }
                >
                  {`Данные заканчиваются на странице ${formatNumber(lastPage)}: всего ${formatNumber(total)}.`}
                </EmptyState>
              ) : data.rows?.length ? (
                <DataTable key={module} rows={data.rows} columns={data.columns} onDetail={setSelected} />
              ) : 'provider_usage' in data ? null : (
                <EmptyState title="По этим условиям данных нет">
                  Измените фильтры или выберите другой набор.
                </EmptyState>
              )}
              {pagesBackend(definition) && data.rows?.length > 0 && (
                <StatsPager
                  caption={pageCaption(pageNo, data.rows.length, total)}
                  page={pageNo}
                  pageCount={lastPage}
                  onPage={next => go({ page: next === 1 ? null : next })}
                />
              )}
            </div>
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
