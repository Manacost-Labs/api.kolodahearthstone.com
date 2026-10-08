import type { ReactNode } from 'react';
import { ArrowSquareOutIcon } from '@phosphor-icons/react/dist/ssr/ArrowSquareOut';
import { TrayIcon } from '@phosphor-icons/react/dist/ssr/Tray';
import {
  fieldLabel,
  formatDateTime,
  formatRelative,
  formatValue,
  inferKind,
  parseDate,
  statusMeta,
  type Tone,
  type ValueKind,
} from '@/lib/format';
import { decode, mediaUrl } from '@/lib/model';
import { Thumb } from './Thumb';

const valueKinds: readonly string[] = [
  'text',
  'number',
  'percent',
  'date',
  'status',
  'boolean',
  'link',
  'image',
  'code',
  'card_type',
  'creature_type',
  'rarity',
];
const isValueKind = (value: unknown): value is ValueKind =>
  typeof value === 'string' && valueKinds.includes(value);

export function StatusBadge({ value, label, tone }: { value?: unknown; label?: string; tone?: Tone }) {
  const meta = statusMeta(value);
  return <span className={`badge ${tone ?? meta.tone}`}>{label ?? meta.label}</span>;
}

// Exact UTC time in the tooltip; relative wording ("5 минут назад") when asked.
// The relative text depends on the clock, so server and client may differ by a minute.
export function Time({ value, relative = false }: { value: unknown; relative?: boolean }) {
  const date = parseDate(value);
  if (!date) return <span className="value-empty">—</span>;
  const exact = formatDateTime(date);
  return (
    <time dateTime={date.toISOString()} title={exact} suppressHydrationWarning>
      {relative ? formatRelative(date) : exact}
    </time>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <TrayIcon className="empty-icon" size={28} aria-hidden="true" />
      <h2>{title}</h2>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function KpiCard({
  label,
  value,
  hint,
  tone,
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone;
}) {
  return (
    <article className="kpi" data-tone={tone && tone !== 'neutral' ? tone : undefined}>
      <span>{label}</span>
      <strong>{value}</strong>
      {hint && <small>{hint}</small>}
    </article>
  );
}

const audioPattern = /\.(mp3|ogg|wav)(\?|$)/i;

/** Renders one field the same way everywhere: tables, record details and statistics extras. */
export function Value({ value, name = '', kind }: { value: unknown; name?: string; kind?: string }) {
  const decoded = decode(value);
  if (decoded !== null && typeof decoded === 'object')
    return (
      <details className="nested-data">
        <summary>{Array.isArray(decoded) ? `${decoded.length} записей` : 'Объект'}</summary>
        <pre>{JSON.stringify(decoded, null, 2)}</pre>
      </details>
    );
  if (decoded === null || decoded === undefined || decoded === '')
    return <span className="value-empty">—</span>;
  const url = mediaUrl(decoded);
  if (url && audioPattern.test(url))
    // biome-ignore lint/a11y/useMediaCaption: card sound effects have no speech to caption
    return <audio aria-label="Аудиозапись" controls preload="none" src={url} />;
  const resolved = isValueKind(kind) ? kind : inferKind(name, decoded);
  switch (resolved) {
    case 'status':
      return <StatusBadge value={decoded} />;
    case 'date':
      return <Time value={decoded} />;
    case 'code':
      return <code className="value-code">{String(decoded)}</code>;
    case 'number':
    case 'percent':
      return <span className="value-number">{formatValue(resolved, decoded)}</span>;
    case 'image':
      if (url) return <Thumb url={url} alt={name ? fieldLabel(name) : 'Изображение'} />;
      break;
    case 'link':
      if (url)
        return (
          <a className="value-link" href={url} target="_blank" rel="noopener noreferrer">
            Открыть <ArrowSquareOutIcon size={14} aria-hidden="true" />
          </a>
        );
      break;
  }
  if (url)
    return (
      <a className="value-link" href={url} target="_blank" rel="noopener noreferrer">
        {url}
      </a>
    );
  return <span>{formatValue(resolved, decoded)}</span>;
}
