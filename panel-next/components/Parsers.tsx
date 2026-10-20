'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiPath } from '@/lib/client';
import { formatDateTime, toneOf } from '@/lib/format';
import { record, text } from '@/lib/model';
import type { Row } from '@/lib/types';
import parserView from '@/lib/vendor/parser-control-view.cjs';
import { Modal } from './Modal';
import { RecordDetails } from './Details';
import { KpiCard, StatusBadge, Time } from './ui';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { ArrowClockwiseIcon } from '@phosphor-icons/react/dist/ssr/ArrowClockwise';
import { Select } from './varylo-ui/forms/Select';
import { Tabs } from './varylo-ui/navigation/Tabs';
export function Parsers({ initial, csrf }: { initial: Row; csrf: string }) {
  const [snapshot, setSnapshot] = useState(initial);
  const [query, setQuery] = useState('');
  const [section, setSection] = useState('all');
  const [status, setStatus] = useState('all');
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
          <h1>Парсеры и источники</h1>
          <p>Свежесть, расписания и история запусков.</p>
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
        <KpiCard label="Свежие наборы" value={summary.fresh} tone="good" />
        <KpiCard
          label="На резерве"
          value={summary.fallback}
          tone={Number(summary.fallback) > 0 ? 'warning' : undefined}
        />
        <KpiCard
          label="Без данных"
          value={summary.unavailable}
          tone={Number(summary.unavailable) > 0 ? 'bad' : undefined}
        />
        <KpiCard
          label="Следующий запуск"
          value={<Time value={summary.nextRunAt} relative />}
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
            { value: 'all', label: 'Все состояния' },
            { value: 'fresh', label: 'Свежие данные' },
            { value: 'fallback', label: 'На резерве' },
            { value: 'unavailable', label: 'Данных нет' },
            { value: 'disabled', label: 'Отключены' },
          ]}
        />
      </div>
      <Tabs
        className="section-tabs"
        label="Разделы источников"
        value={section}
        onValueChange={setSection}
        items={[
          { value: 'all', label: 'Все', badge: sources.length },
          ...sections.map(item => ({ value: text(item.id), label: text(item.label || item.id) })),
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
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Источник</th>
              <th>Состояние данных</th>
              <th>Строк</th>
              <th>Последний успех</th>
              <th>Следующий запуск</th>
              <th>Действия</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(source => {
              const presentation = parserView.sourcePresentation(source);
              return (
                <tr key={text(source.id)}>
                  <td>
                    <strong>{text(source.label || source.name || source.id)}</strong>
                    <small>{text(source.sectionLabel)}</small>
                  </td>
                  <td>
                    <StatusBadge tone={toneOf(presentation.tone)} label={presentation.label} />
                    <small>{presentation.description}</small>
                  </td>
                  <td>{text(source.rowsTotal)}</td>
                  <td>
                    <Time value={source.lastSuccessAt} relative />
                  </td>
                  <td>
                    <Time value={source.nextRunAt} relative />
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
        {!rows.length && <p className="empty table-empty">Источники не найдены.</p>}
      </div>
      <details className="extra-data">
        <summary>История запусков</summary>
        <pre>{JSON.stringify(snapshot.recentRuns || snapshot.runs || [], null, 2)}</pre>
      </details>
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
