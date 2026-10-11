'use client';
import { useEffect, useState } from 'react';
import { type CardHistoryData, historyChanges, historyDate } from '@/lib/card-history';
import { fieldLabel } from '@/lib/format';
import { Value } from './ui';

const eventNames: Record<string, string> = {
  baseline: 'Начальный снимок',
  new: 'Добавлена',
  changed: 'Изменена',
  removed: 'Удалена из каталога',
  renamed: 'Изменён идентификатор',
};
export function CardHistory({ entity, cardId }: { entity: string; cardId: string }) {
  const [legacy, setLegacy] = useState(false);
  const [data, setData] = useState<CardHistoryData | null>(null);
  const [request, setRequest] = useState({ page: 1 });
  const page = request.page;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [before, setBefore] = useState(0);
  const [after, setAfter] = useState(0);
  useEffect(() => {
    const page = request.page;
    const controller = new AbortController();
    const query = new URLSearchParams({
      action: 'card_history',
      entity,
      card_id: cardId,
      page: String(page),
      legacy: legacy ? '1' : '0',
    });
    setBusy(true);
    setError('');
    void fetch((process.env.NEXT_PUBLIC_PANEL_BASE_PATH || '') + '/api/panel?' + query, {
      signal: controller.signal,
      cache: 'no-store',
    })
      .then(async response => {
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Не удалось загрузить историю.');
        if (controller.signal.aborted) return;
        const incoming: CardHistoryData = payload.data;
        setData(previous =>
          page === 1 || !previous
            ? incoming
            : {
                ...incoming,
                versions: [...previous.versions, ...incoming.versions].filter(
                  (version, index, all) =>
                    all.findIndex(item => item.version_id === version.version_id) === index,
                ),
              },
        );
        if (page === 1) {
          setAfter(incoming.versions[0]?.version_id || 0);
          setBefore(incoming.versions[1]?.version_id || 0);
        }
      })
      .catch(cause => {
        if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Ошибка загрузки.');
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [entity, cardId, legacy, request]);
  const versions = data?.versions || [];
  const oldVersion = versions.find(version => version.version_id === before);
  const newVersion = versions.find(version => version.version_id === after);
  const changes = oldVersion && newVersion ? historyChanges(oldVersion.snapshot, newVersion.snapshot) : [];
  const versionLabel = (version: (typeof versions)[number]) =>
    historyDate(version.observed_at) + ' · ' + (eventNames[version.event] || version.event);
  const select = (label: string, value: number, update: (value: number) => void) => (
    <label>
      <span>{label}</span>
      <select value={value} onChange={event => update(Number(event.target.value))}>
        <option value={0}>Выберите версию</option>
        {versions.map(version => (
          <option key={version.version_id} value={version.version_id}>
            {versionLabel(version)}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <div className="card-history">
      <p className="muted">
        Сохраняем наблюдённые изменения. Даты показывают момент записи, а не дату выхода патча. До начала
        записи история может быть неполной. Изображения сохранены ссылками.
      </p>
      {entity === 'card' && (
        <label className="history-source">
          <span>Источник истории</span>
          <select
            value={legacy ? 'legacy' : 'catalog'}
            onChange={event => {
              setData(null);
              setRequest({ page: 1 });
              setLegacy(event.target.value === 'legacy');
            }}
          >
            <option value="catalog">Снимки каталога</option>
            <option value="legacy">Старый журнал Полей сражений</option>
          </select>
        </label>
      )}
      {error && (
        <div role="alert" className="notice bad">
          {error}{' '}
          <button type="button" onClick={() => setRequest(value => ({ ...value }))}>
            Повторить
          </button>
        </div>
      )}
      {busy && <p role="status">Загружаем версии…</p>}
      {data && <p role="status">Сохранено версий: {data.pagination.total}</p>}
      {!busy && !error && data && !versions.length && (
        <p className="empty">Для этой карты ещё нет сохранённых версий.</p>
      )}
      {versions.length > 0 && (
        <>
          <div className="history-comparison-controls">
            {select('До', before, setBefore)}
            {select('После', after, setAfter)}
          </div>
          {oldVersion && newVersion && (
            <section aria-label="Различия версий">
              <p>
                {versionLabel(oldVersion)} → {versionLabel(newVersion)}
              </p>
              {newVersion.event === 'removed' && (
                <p className="notice">Карта удалена из каталога; её последний снимок сохранён.</p>
              )}
              {changes.length ? (
                <div className="table-scroll">
                  <table className="history-diff">
                    <thead>
                      <tr>
                        <th>Поле</th>
                        <th>До</th>
                        <th>После</th>
                      </tr>
                    </thead>
                    <tbody>
                      {changes.map(change => (
                        <tr key={change.key}>
                          <th scope="row">{fieldLabel(change.key)}</th>
                          <td>
                            <Value value={change.before} name={change.key} />
                          </td>
                          <td>
                            <Value value={change.after} name={change.key} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p>Характеристики и текст совпадают.</p>
              )}
            </section>
          )}
          <ol className="history-timeline">
            {versions.map(version => (
              <li key={version.version_id}>
                <span>{versionLabel(version)}</span>
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => {
                    setAfter(version.version_id);
                    setBefore(versions[versions.indexOf(version) + 1]?.version_id || 0);
                  }}
                >
                  Сравнить с предыдущей
                </button>
                <details>
                  <summary>Полный снимок</summary>
                  <pre>{JSON.stringify(version.snapshot, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ol>
          {data && page < data.pagination.total_pages && (
            <button
              type="button"
              className="button secondary"
              disabled={busy || !!error}
              onClick={() => setRequest(value => ({ page: value.page + 1 }))}
            >
              Показать более ранние версии
            </button>
          )}
        </>
      )}
    </div>
  );
}

export function CardHistoryFamily({
  entity,
  cardId,
  goldenId,
}: {
  entity: string;
  cardId: string;
  goldenId?: string;
}) {
  const [variant, setVariant] = useState(cardId);
  return (
    <>
      {goldenId && (
        <label className="history-source">
          <span>Версия карты</span>
          <select value={variant} onChange={event => setVariant(event.target.value)}>
            <option value={cardId}>Обычная</option>
            <option value={goldenId}>Золотая</option>
          </select>
        </label>
      )}
      <CardHistory key={variant} entity={entity} cardId={variant} />
    </>
  );
}
