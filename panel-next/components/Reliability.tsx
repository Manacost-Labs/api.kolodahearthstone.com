'use client';
import { useState } from 'react';
import view from '@/lib/vendor/parsing-reliability.cjs';
import { normalizeShares, windowLabel } from '@/lib/format';
import { record, text } from '@/lib/model';
import { StatusBadge, Value } from './ui';
import { Select } from './varylo-ui/forms/Select';
function shares(value: unknown): unknown {
  if (typeof value === 'string') return normalizeShares(value);
  if (Array.isArray(value)) return value.map(shares);
  if (value && typeof value === 'object')
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, shares(item)]));
  return value;
}
export function Reliability({ data }: { data: unknown }) {
  const [window, setWindow] = useState('24h');
  // The vendored model writes shares its own way (`98,19%`); the panel shows one shape everywhere.
  const model = shares(view.buildReliabilityViewModel(data, window)) as ReturnType<
    typeof view.buildReliabilityViewModel
  >;
  const windows = Array.isArray(model.windows) ? model.windows.map(String) : [];
  const quality = record(model.verifiedCompleteness);
  return (
    <section className="reliability">
      <div className="data-status">
        <h2>Надёжность сбора</h2>
        <StatusBadge tone="info" label={text(model.badge)} />
        <Select
          aria-label="Окно надёжности"
          size="sm"
          className="reliability-window"
          value={window}
          onValueChange={setWindow}
          options={(windows.length ? windows : ['24h']).map(w => ({ value: w, label: windowLabel(w) }))}
        />
      </div>
      <div className="summary-grid">
        {[
          ['Полный свежий сбор', model.fullFresh],
          ['Доступность данных', model.availability],
          ['Принятая свежесть', model.acceptedFresh],
          ['Покрытие наблюдений', model.coverage],
        ].map(([name, value]) => (
          <article key={String(name)}>
            <span>{text(name)}</span>
            <strong>{text(value)}</strong>
          </article>
        ))}
      </div>
      <p className="muted">{text(model.message)}</p>
      <div className="data-status">
        <span>Попыток: {text(model.totalAttempts)}</span>
        <span>Подходящих: {text(model.eligibleAttempts)}</span>
        <span>Ошибки: {text(record(model.counts).failed)}</span>
        <span>Тайм-ауты: {text(record(model.counts).timedOut)}</span>
        <span>LKG: {text(record(model.counts).lkg)}</span>
      </div>
      <details className="extra-data">
        <summary>Полнота извлечения и публикации</summary>
        <Value value={quality} />
        <Value value={model.scheduledReliability} />
        <Value value={model.freshnessSlo} />
      </details>
    </section>
  );
}
