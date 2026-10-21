'use client';
import { useMemo, useState } from 'react';
import { CaretDownIcon } from '@phosphor-icons/react/dist/ssr/CaretDown';
import { ColumnsIcon } from '@phosphor-icons/react/dist/ssr/Columns';
import { CaretUpIcon } from '@phosphor-icons/react/dist/ssr/CaretUp';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { countLabel, rowMatches, rowNeedsAttention } from '@/lib/format';
import type { Row } from '@/lib/types';
import { EmptyState, Value } from './ui';
import { useUrlState } from './useUrlState';
import { DropdownMenu } from './varylo-ui/navigation/DropdownMenu';
import { Pagination } from './varylo-ui/navigation/Pagination';
import { SegmentedControl } from './varylo-ui/navigation/SegmentedControl';

const PAGE = 25;
export function DataTable({
  rows,
  columns,
  onDetail,
}: {
  rows: Row[];
  columns: { key: string; label: string; type?: string }[];
  onDetail?: (row: Row) => void;
}) {
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);
  const [page, setPage] = useState(1);
  const [hidden, setHidden] = useState<string[]>([]);
  // Search and the attention filter are kept in the URL (rows_q, rows=attention); page.tsx strips them.
  const [query, setQuery] = useUrlState('rows_q');
  const [only, setOnly] = useUrlState('rows', 'all');
  const rowKeys = useMemo(() => new Map(rows.map((row, index) => [row, index])), [rows]);
  const attention = useMemo(() => rows.filter(row => rowNeedsAttention(row, columns)), [rows, columns]);
  const filtered = useMemo(
    () => (only === 'attention' ? attention : rows).filter(row => rowMatches(row, columns, query)),
    [rows, attention, columns, only, query],
  );
  const ordered = useMemo(
    () =>
      sort
        ? [...filtered].sort((a, b) => {
            const left = a[sort.key],
              right = b[sort.key];
            const numeric =
              left !== null &&
              right !== null &&
              left !== '' &&
              right !== '' &&
              Number.isFinite(Number(left)) &&
              Number.isFinite(Number(right));
            const value = numeric
              ? Number(left) - Number(right)
              : String(left ?? '').localeCompare(String(right ?? ''), 'ru');
            return sort.desc ? -value : value;
          })
        : filtered,
    [filtered, sort],
  );
  const totalPages = Math.max(1, Math.ceil(ordered.length / PAGE));
  const current = Math.min(page, totalPages);
  const visible = columns.filter((c, i) => i === 0 || !hidden.includes(c.key));
  return (
    <>
      <div className="table-tools">
        {rows.length > 8 && (
          <label className="search table-search">
            <MagnifyingGlassIcon size={16} aria-hidden="true" />
            <input
              type="search"
              aria-label="Поиск по таблице"
              placeholder="Поиск по таблице"
              value={query}
              onChange={event => {
                setPage(1);
                setQuery(event.target.value);
              }}
            />
          </label>
        )}
        {(attention.length > 0 || only === 'attention') && (
          <SegmentedControl
            aria-label="Какие строки показывать"
            size="sm"
            value={only}
            onValueChange={value => {
              setPage(1);
              setOnly(value);
            }}
            options={[
              { value: 'all', label: `Все · ${rows.length}` },
              { value: 'attention', label: `Требуют внимания · ${attention.length}` },
            ]}
          />
        )}
        <span className="table-count" role="status">
          {filtered.length === rows.length
            ? countLabel(rows.length, { one: 'запись', few: 'записи', many: 'записей' })
            : `${filtered.length} из ${rows.length}`}
        </span>
        {columns.length > 1 && (
          <DropdownMenu
            placement="bottom-end"
            items={[
              { type: 'label', label: 'Показывать колонки' },
              ...columns.slice(1).map(col => ({
                type: 'checkbox' as const,
                label: col.label,
                checked: !hidden.includes(col.key),
                onCheckedChange: (checked: boolean) =>
                  setHidden(checked ? hidden.filter(k => k !== col.key) : [...hidden, col.key]),
              })),
            ]}
            trigger={
              <button type="button" className="button secondary columns-trigger">
                <ColumnsIcon size={16} aria-hidden="true" /> Колонки
                {hidden.length > 0 && <span className="columns-count">−{hidden.length}</span>}
              </button>
            }
          />
        )}
      </div>
      {/* Focusable so keyboard users can scroll a table wider than the screen. */}
      {/* biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable regions must be reachable by keyboard */}
      <div className="table-scroll" role="region" tabIndex={0} aria-label="Таблица данных">
        <table>
          <thead>
            <tr>
              {visible.map(col => (
                <th
                  key={col.key}
                  aria-sort={sort?.key === col.key ? (sort.desc ? 'descending' : 'ascending') : 'none'}
                >
                  <button
                    type="button"
                    onClick={() => {
                      setPage(1);
                      setSort({ key: col.key, desc: sort?.key === col.key ? !sort.desc : false });
                    }}
                  >
                    {col.label}
                    {sort?.key === col.key &&
                      (sort.desc ? (
                        <CaretDownIcon size={12} aria-hidden="true" />
                      ) : (
                        <CaretUpIcon size={12} aria-hidden="true" />
                      ))}
                  </button>
                </th>
              ))}
              {onDetail && <th>Детали</th>}
            </tr>
          </thead>
          <tbody>
            {ordered.slice((current - 1) * PAGE, current * PAGE).map(row => (
              <tr key={rowKeys.get(row)}>
                {visible.map(col => (
                  <td key={col.key}>
                    {col.type === 'text' && typeof row[col.key] === 'string' ? (
                      // Long descriptions stay readable at three lines; the full text is in the tooltip and details.
                      <span className="cell-clamp" title={String(row[col.key])}>
                        <Value value={row[col.key]} name={col.key} kind={col.type} />
                      </span>
                    ) : (
                      <Value value={row[col.key]} name={col.key} kind={col.type} />
                    )}
                  </td>
                ))}
                {onDetail && (
                  <td>
                    <button type="button" className="link-button" onClick={() => onDetail(row)}>
                      Подробнее
                    </button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
        {!ordered.length && (
          <EmptyState title="Ничего не найдено">Измените запрос или покажите все строки.</EmptyState>
        )}
      </div>
      {totalPages > 1 && (
        <div className="table-pagination">
          <Pagination
            aria-label="Страницы таблицы"
            pageCount={totalPages}
            value={current}
            onValueChange={setPage}
            labels={{
              previous: 'Предыдущая страница',
              next: 'Следующая страница',
              page: page => `Страница ${page}`,
            }}
          />
          <span>
            Страница {current} из {totalPages}
          </span>
        </div>
      )}
    </>
  );
}
