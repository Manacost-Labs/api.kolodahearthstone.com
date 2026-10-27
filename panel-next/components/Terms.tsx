'use client';
import { useState } from 'react';
import { mutate } from '@/lib/client';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { countLabel } from '@/lib/format';
import { text } from '@/lib/model';
import type { PageData } from '@/lib/types';
import { EmptyState } from './ui';
export function Terms({ data }: { data: PageData }) {
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; bad: boolean } | null>(null);
  const groups = Object.entries(data.terms || {});
  const needle = query.trim().toLowerCase();
  // Filtering only hides rows: every term stays in the form, so saving never drops hidden ones.
  const matches = (row: Record<string, unknown>) =>
    !needle ||
    text(row.term_en).toLowerCase().includes(needle) ||
    text(row.term_ru, '').toLowerCase().includes(needle);
  const total = groups.reduce((sum, [, rows]) => sum + rows.length, 0);
  const shown = groups.reduce((sum, [, rows]) => sum + rows.filter(matches).length, 0);
  const missing = groups.reduce(
    (sum, [, rows]) => sum + rows.filter(row => !text(row.term_ru, '')).length,
    0,
  );
  return (
    <section>
      <header className="page-head">
        <div>
          <h1>Переводы Wiki</h1>
          <p>
            Словарь EN → RU для терминов Wiki:{' '}
            {countLabel(total, { one: 'термин', few: 'термина', many: 'терминов' })}
            {missing ? `, без перевода ${missing}` : ''}.
          </p>
        </div>
      </header>
      <label className="search">
        <MagnifyingGlassIcon size={16} aria-hidden="true" />
        <input
          type="search"
          aria-label="Поиск термина"
          placeholder="Термин на английском или русском"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
      </label>
      {status && (
        <p role={status.bad ? 'alert' : 'status'} className={status.bad ? 'notice bad' : 'notice'}>
          {status.text}
        </p>
      )}
      <form
        onSubmit={async e => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          setBusy(true);
          try {
            const result = await mutate(form);
            setStatus({ text: result.message || 'Переводы сохранены.', bad: false });
          } catch (error) {
            setStatus({ text: error instanceof Error ? error.message : 'Ошибка сохранения.', bad: true });
          } finally {
            setBusy(false);
          }
        }}
      >
        <input type="hidden" name="action" value="save_wiki_terms" />
        <input type="hidden" name="csrf" value={data.csrf} />
        <div className="terms-grid">
          {groups.map(([type, rows]) => (
            <section key={type} className="panel-card" hidden={!rows.some(matches)}>
              <h2>{data.termLabels?.[type] || type}</h2>
              {rows.map((row, i) => (
                <label key={text(row.term_en)} hidden={!matches(row)}>
                  <span>{text(row.term_en)}</span>
                  <input type="hidden" name={`terms[${type}][${i}][en]`} value={text(row.term_en)} />
                  <input
                    name={`terms[${type}][${i}][ru]`}
                    defaultValue={text(row.term_ru, '')}
                    placeholder="Русский перевод"
                  />
                </label>
              ))}
            </section>
          ))}
        </div>
        {!shown && (
          <EmptyState title={total ? 'Термины не найдены' : 'Терминов пока нет'}>
            {total ? `По запросу «${query.trim()}» ничего нет.` : 'Словарь пока пуст.'}
          </EmptyState>
        )}
        <div className="form-actions sticky-actions">
          <button type="submit" className="button" disabled={busy}>
            {busy ? 'Сохраняем…' : 'Сохранить переводы'}
          </button>
          {needle && total > 0 && (
            <span className="form-actions-note">
              Показано {shown} из {total}; скрытые термины тоже сохранятся.
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
