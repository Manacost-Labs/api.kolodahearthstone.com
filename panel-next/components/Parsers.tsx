'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiPath } from '@/lib/client';
import { countLabel, formatDateTime, formatSchedule, toneOf } from '@/lib/format';
import { formatDuration } from '@/lib/provider-usage';
import { record, text } from '@/lib/model';
import type { Row } from '@/lib/types';
import parserView from '@/lib/vendor/parser-control-view.cjs';
import { Modal } from './Modal';
import { RecordDetails } from './Details';
import { EmptyState, KpiCard, ScheduleTime, StatusBadge, Time } from './ui';
import { useUrlState } from './useUrlState';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { ArrowClockwiseIcon } from '@phosphor-icons/react/dist/ssr/ArrowClockwise';
import { Select } from './varylo-ui/forms/Select';
import { Tabs } from './varylo-ui/navigation/Tabs';
export function Parsers({ initial, csrf }: { initial: Row; csrf: string }) {
  const [snapshot, setSnapshot] = useState(initial);
  // Filters live in the address bar: a link to "sources on fallback" opens exactly that list.
  const [query, setQuery] = useUrlState('q');
  const [section, setSection] = useUrlState('section', 'all');
  const [status, setStatus] = useUrlState('status', 'all');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [detail, setDetail] = useState<Row | null>(null);
  const [confirmation, setConfirmation] = useState<{ title: string; payload: Row } | null>(null);
  const activeRequest = useRef(false);
  const refresh = useCallback(async () => {
    if (activeRequest.current) return;
    activeRequest.current = true;
    try {
      const response = await fetch(apiPath + '?view=parsers', { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || 'Не удалось обновить состояние.');
      setSnapshot(payload.data);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка загрузки.');
    } finally {
      activeRequest.current = false;
    }
  }, []);
  const running = Boolean(snapshot.activeRun);
  useEffect(() => {
    const interval = setInterval(
      () => {
        if (document.visibilityState === 'visible') void refresh();
      },
      running ? 5000 : 30000,
    );
    return () => clearInterval(interval);
  }, [running, refresh]);
  const sources = parserView.flattenSources(snapshot);
  const summary = parserView.buildSummary(snapshot);
  const sections = Array.isArray(snapshot.sections) ? snapshot.sections.map(record) : [];
  const labels = new Map(sources.map(source => [text(source.id), text(source.label || source.id)]));
  const filterTo = (value: string) => setStatus(status === value ? 'all' : value);
  const statusCount = (value: string) =>
    sources.filter(source => parserView.sourcePresentation(source).filter === value).length;
  const rows = sources.filter(
    source =>
      (section === 'all' || source.sectionId === section) &&
      (status === 'all' || parserView.sourcePresentation(source).filter === status) &&
      text(source.label || source.name || source.id, '')
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const run = async () => {
    if (!confirmation) return;
    setBusy(true);
    setError('');
    try {
      const response = await fetch(apiPath + '?view=parsers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify(confirmation.payload),
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || 'Запуск не выполнен.');
      setConfirmation(null);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Ошибка запуска.');
    } finally {
      setBusy(false);
    }
  };
  return (
    <section>
      <header className="page-head">
        <div>
          <h1>Источники</h1>
          <p>Парсеры: свежесть данных, расписания и история запусков.</p>
        </div>
        <button className="button secondary" type="button" onClick={() => void refresh()}>
          <ArrowClockwiseIcon size={16} aria-hidden="true" /> Обновить
        </button>
      </header>
      {error && (
        <p className="notice bad" role="alert">
          {error}
        </p>
      )}
      <div className="summary-grid">
        <KpiCard
          label="Свежие наборы"
          value={summary.fresh}
          tone="good"
          pressed={status === 'fresh'}
          onSelect={() => filterTo('fresh')}
        />
        <KpiCard
          label="На резерве"
          value={summary.fallback}
          tone={Number(summary.fallback) > 0 ? 'warning' : undefined}
          hint={Number(summary.fallback) > 0 ? 'API отдаёт прошлый успешный набор' : undefined}
          pressed={status === 'fallback'}
          onSelect={() => filterTo('fallback')}
        />
        <KpiCard
          label="Без данных"
          value={summary.unavailable}
          tone={Number(summary.unavailable) > 0 ? 'bad' : undefined}
          pressed={status === 'unavailable'}
          onSelect={() => filterTo('unavailable')}
        />
        <KpiCard
          label="Следующий запуск"
          value={<ScheduleTime value={summary.nextRunAt} />}
          tone={formatSchedule(summary.nextRunAt).overdue ? 'warning' : undefined}
          hint={summary.nextRunAt ? formatDateTime(summary.nextRunAt) : undefined}
        />
      </div>
      {summary.activeRun && (
        <div className="notice" role="status">
          Сейчас выполняется: {text(summary.activeRun.id)} ·{' '}
          {parserView.runProgress(summary.activeRun).percent}%
        </div>
      )}
      <div className="toolbar-row">
        <label className="search">
          <MagnifyingGlassIcon size={16} aria-hidden="true" />
          <input
            type="search"
            aria-label="Поиск источника"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Название или ID источника"
          />
        </label>
        <Select
          aria-label="Состояние источника"
          size="sm"
          className="toolbar-select"
          value={status}
          onValueChange={setStatus}
          options={[
            { value: 'all', label: `Все состояния · ${sources.length}` },
            { value: 'fresh', label: `Свежие данные · ${statusCount('fresh')}` },
            { value: 'fallback', label: `На резерве · ${statusCount('fallback')}` },
            { value: 'unavailable', label: `Данных нет · ${statusCount('unavailable')}` },
            { value: 'disabled', label: `Отключены · ${statusCount('disabled')}` },
          ]}
        />
        {(status !== 'all' || query) && (
          <button
            type="button"
            className="link-button"
            onClick={() => {
              setStatus('all');
              setQuery('');
            }}
          >
            Сбросить
          </button>
        )}
        <span className="toolbar-count" role="status">
          {countLabel(rows.length, { one: 'источник', few: 'источника', many: 'источников' })}
        </span>
      </div>
      <Tabs
        className="section-tabs"
        label="Разделы источников"
        value={section}
        onValueChange={setSection}
        items={[
          { value: 'all', label: 'Все', badge: sources.length },
          ...sections.map(item => ({
            value: text(item.id),
            label: text(item.label || item.id),
            badge: sources.filter(source => source.sectionId === item.id).length,
          })),
        ]}
      />
      {section !== 'all' && (
        <div className="form-actions">
          <button
            className="button"
            type="button"
            onClick={() =>
              setConfirmation({
                title: 'Запустить раздел?',
                payload: { action: 'run', section_ids: [section] },
              })
            }
          >
            Запустить раздел
          </button>
          <button
            className="button secondary"
            type="button"
            onClick={() =>
              setConfirmation({
                title: 'Изменить расписание раздела?',
                payload: {
                  action: 'section',
                  section_id: section,
                  revision: Number(record(snapshot.config).revision || snapshot.revision),
                  enabled: sections.find(s => s.id === section)?.enabled === false,
                },
              })
            }
          >
            {sections.find(s => s.id === section)?.enabled === false
              ? 'Включить расписание'
              : 'Отключить расписание'}
          </button>
        </div>
      )}
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable regions must be reachable by keyboard */}
      <div className="table-scroll" role="region" tabIndex={0} aria-label="Источники">
        <table className="sources-table">
          <thead>
            <tr>
              <th scope="col">Источник</th>
              <th scope="col">Состояние данных</th>
              <th scope="col" className="num">
                Строк
              </th>
              <th scope="col">Последний успех</th>
              <th scope="col">Следующий запуск</th>
              <th scope="col">
                <span className="sr-only">Действия</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(source => {
              const presentation = parserView.sourcePresentation(source);
              // Healthy rows stay quiet; only a problem explains itself, with the parser's reason.
              const reason =
                presentation.filter === 'fresh'
                  ? ''
                  : parserView.errorSummary(source.lastError) || presentation.description;
              return (
                <tr key={text(source.id)} data-tone={toneOf(presentation.tone)}>
                  <th scope="row">
                    <strong>{text(source.label || source.name || source.id)}</strong>
                    <small>{text(source.sectionLabel)}</small>
                  </th>
                  <td data-label="Состояние">
                    <StatusBadge tone={toneOf(presentation.tone)} label={presentation.label} />
                    {reason && <small>{reason}</small>}
                  </td>
                  <td className="num" data-label="Строк">
                    {parserView.formatNumber(source.rowsTotal)}
                  </td>
                  <td data-label="Последний успех">
                    <Time value={source.lastSuccessAt} relative />
                  </td>
                  <td data-label="Следующий запуск">
                    <ScheduleTime value={source.nextRunAt} />
                  </td>
                  <td>
                    <div className="row-actions">
                      <button type="button" className="link-button" onClick={() => setDetail(source)}>
                        Детали
                      </button>
                      <button
                        type="button"
                        className="button secondary"
                        disabled={presentation.filter === 'disabled' || !!summary.activeRun}
                        onClick={() =>
                          setConfirmation({
                            title: 'Запустить источник?',
                            payload: { action: 'run', source_ids: [source.id] },
                          })
                        }
                      >
                        Запустить
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && (
          <EmptyState title="Источники не найдены">Измените запрос или выберите другое состояние.</EmptyState>
        )}
      </div>
      <RunHistory
        runs={(Array.isArray(snapshot.recentRuns) ? snapshot.recentRuns : []).map(record)}
        labels={labels}
        onDetail={setDetail}
      />
      {detail && (
        <RecordDetails row={detail} title={text(detail.label || detail.id)} onClose={() => setDetail(null)} />
      )}
      {confirmation && (
        <Modal title={confirmation.title} onClose={() => setConfirmation(null)}>
          <p>Действие будет выполнено от вашего имени. Расписание остальных разделов сохранится.</p>
          <div className="form-actions">
            <button type="button" className="button secondary" onClick={() => setConfirmation(null)}>
              Отмена
            </button>
            <button type="button" className="button" disabled={busy} onClick={() => void run()}>
              {busy ? 'Выполняем…' : 'Подтвердить'}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}

function RunHistory({
  runs,
  labels,
  onDetail,
}: {
  runs: Row[];
  labels: Map<string, string>;
  onDetail: (run: Row) => void;
}) {
  return (
    <section className="run-history" aria-labelledby="run-history-title">
      <h2 id="run-history-title">Последние запуски</h2>
      {runs.length ? (
        // biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable regions must be reachable by keyboard
        <div className="table-scroll" role="region" tabIndex={0} aria-label="Последние запуски">
          <table className="runs-table">
            <thead>
              <tr>
                <th scope="col">Результат</th>
                <th scope="col">Источники</th>
                <th scope="col">Начат</th>
                <th scope="col" className="num">
                  Длительность
                </th>
                <th scope="col">Инициатор</th>
                <th scope="col">
                  <span className="sr-only">Действия</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {runs.map(run => {
                const ids = (Array.isArray(run.sourceIds) ? run.sourceIds : []).map(id => text(id));
                const names = ids.slice(0, 2).map(id => labels.get(id) || id);
                const progress = parserView.runProgress(run);
                const started = Date.parse(text(run.startedAt || run.createdAt, ''));
                const finished = Date.parse(text(run.finishedAt, ''));
                return (
                  <tr key={text(run.id)}>
                    <td data-label="Результат">
                      <StatusBadge value={run.status} />
                      {Number(run.failedSources) > 0 && (
                        <small>
                          Ошибок: {text(run.failedSources)} из {progress.total}
                        </small>
                      )}
                    </td>
                    <th scope="row">
                      <strong>{names.join(', ') || '—'}</strong>
                      {ids.length > names.length && <small>и ещё {ids.length - names.length}</small>}
                    </th>
                    <td data-label="Начат">
                      <Time value={run.startedAt || run.createdAt} relative />
                    </td>
                    <td className="num" data-label="Длительность">
                      {Number.isFinite(started) && Number.isFinite(finished)
                        ? formatDuration(finished - started)
                        : '—'}
                    </td>
                    <td data-label="Инициатор">
                      {text(run.requestedBy)}
                      {Boolean(run.reason) && <small>{text(run.reason)}</small>}
                    </td>
                    <td>
                      <button type="button" className="link-button" onClick={() => onDetail(run)}>
                        Детали
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="muted-note">Запусков пока не было.</p>
      )}
    </section>
  );
}
