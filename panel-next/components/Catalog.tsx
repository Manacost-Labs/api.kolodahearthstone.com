'use client';
import { useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ArrowCounterClockwiseIcon } from '@phosphor-icons/react/dist/ssr/ArrowCounterClockwise';
import { SlidersHorizontalIcon } from '@phosphor-icons/react/dist/ssr/SlidersHorizontal';
import { PanelLink } from './WorkspaceNavigation';
import { useCatalogQuery } from './useCatalogQuery';
import { catalogHref, catalogView } from '@/lib/catalog-state';
import { countLabel, formatDay } from '@/lib/format';
import { SquaresFourIcon } from '@phosphor-icons/react/dist/ssr/SquaresFour';
import { RowsIcon } from '@phosphor-icons/react/dist/ssr/Rows';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { ArrowRightIcon } from '@phosphor-icons/react/dist/ssr/ArrowRight';
import { CheckIcon } from '@phosphor-icons/react/dist/ssr/Check';
import { HeartIcon } from '@phosphor-icons/react/dist/ssr/Heart';
import { SparkleIcon } from '@phosphor-icons/react/dist/ssr/Sparkle';
import { StarIcon } from '@phosphor-icons/react/dist/ssr/Star';
import { SwordIcon } from '@phosphor-icons/react/dist/ssr/Sword';
import { TableIcon } from '@phosphor-icons/react/dist/ssr/Table';
import { XIcon } from '@phosphor-icons/react/dist/ssr/X';
import { PlusIcon } from '@phosphor-icons/react/dist/ssr/Plus';
import { Select } from './varylo-ui/forms/Select';
import { SegmentedControl } from './varylo-ui/navigation/SegmentedControl';
import { Pagination } from './varylo-ui/navigation/Pagination';
import { cardKey, matchesCard } from '@/lib/card-detail';
import { catalogColumns, type Cell } from '@/lib/catalog-columns';
import { normalizedCard, queryHref, entityApiBase, text } from '@/lib/model';
import type { PageData, Row } from '@/lib/types';
import { CardInspector } from './CardInspector';
import { Artwork } from './Artwork';
import { DeckTiles } from './DeckTiles';
import { EmptyState, StatusBadge, Time } from './ui';

export function Catalog({
  data,
  query,
  focus = null,
}: {
  data: PageData;
  query: string;
  focus?: Row | null;
}) {
  const { pending, params, search, changeSearch, go, mode, view } = useCatalogQuery(query);
  const [filtersOpen, setFiltersOpen] = useState(false);
  // The open card lives in the URL (?card=…), so it can be shared, reloaded and closed with Back.
  // history.pushState is synced by the Next router and needs no server round trip.
  const cardParam = useSearchParams().get('card') || '';
  const openedHere = useRef(false);
  const cardUrl = (key: string | null) => {
    const next = new URLSearchParams(window.location.search);
    if (key) next.set('card', key);
    else next.delete('card');
    return window.location.pathname + (next.size ? `?${next}` : '');
  };
  const setSelected = (row: Row) => {
    openedHere.current = true;
    window.history.pushState(null, '', cardUrl(cardKey(row)));
  };
  const showCard = (row: Row) => window.history.replaceState(null, '', cardUrl(cardKey(row)));
  const closeCard = () => {
    if (openedHere.current) {
      openedHere.current = false;
      window.history.back();
    } else window.history.replaceState(null, '', cardUrl(null));
  };
  const type = data.cardType;
  const filterType = params.get('card_type') || '';
  const resultView = catalogView(new URLSearchParams(query).get('view'));
  const resetHref = queryHref('', {
    card_type: filterType,
    view,
    per_page: resultView === 'tiles' ? 15 : data.perPage,
  });
  const select = (name: string, label: string, anyLabel: string, options: Record<string, string>) => (
    <div className="filter-field">
      <span aria-hidden="true">{label}</span>
      <Select
        aria-label={label}
        size="sm"
        value={params.get(name) || ''}
        onValueChange={value => go({ [name]: value })}
        options={[
          { value: '', label: anyLabel },
          ...Object.entries(options).map(([key, optionLabel]) => ({ value: key, label: optionLabel })),
        ]}
      />
    </div>
  );
  const filterKeys = ['tier', 'creature_type', 'pool', 'duos', 'constructed_format', 'rarity', 'media'];
  const activeCount = Math.max(data.activeFilters.length, filterKeys.filter(key => params.get(key)).length);
  const cards = data.records.map(row => normalizedCard(row, type, data.tribes));
  const columns = catalogColumns(type, data.tribes);
  const selectedIndex = cardParam ? cards.findIndex(card => matchesCard(card.row, cardParam)) : -1;
  const selected =
    selectedIndex >= 0 ? cards[selectedIndex].row : focus && matchesCard(focus, cardParam) ? focus : null;
  return (
    <section className="catalog">
      <header className="page-head">
        <div>
          <h1>{data.title}</h1>
          <p>
            {data.total
              ? `${countLabel(data.total, { one: 'запись', few: 'записи', many: 'записей' })} в разделе`
              : 'Записей пока нет'}
          </p>
        </div>
        {['', 'minion', 'spell'].includes(type) && (
          <PanelLink className="button" href="/?action=new" prefetch={false}>
            <PlusIcon size={16} aria-hidden="true" /> Добавить карту
          </PanelLink>
        )}
      </header>
      <form
        className="catalog-filters"
        action="/"
        method="get"
        onSubmit={e => {
          e.preventDefault();
          const form = new FormData(e.currentTarget);
          go(Object.fromEntries([...form.entries()].map(([k, v]) => [k, String(v)])), true);
        }}
      >
        <div className="filter-search">
          <label className="search">
            <MagnifyingGlassIcon size={16} aria-hidden="true" />
            <input
              type="search"
              name="q"
              value={search}
              placeholder="Название, ID, DBF, текст или механика"
              aria-label="Поиск карт"
              onChange={e => changeSearch(e.target.value)}
            />
            <kbd>/</kbd>
          </label>
          <button
            type="button"
            className="button secondary filters-toggle"
            aria-expanded={filtersOpen}
            aria-controls="catalog-filter-fields"
            onClick={() => setFiltersOpen(open => !open)}
          >
            <SlidersHorizontalIcon size={16} aria-hidden="true" />
            Фильтры{activeCount ? ` · ${activeCount}` : ''}
          </button>
        </div>
        <nav className="category-tabs" aria-label="Категории каталога">
          {[['', 'Поля сражений'] as const, ...Object.entries(data.categories)].map(([key, label]) => (
            <PanelLink
              key={key || 'battlegrounds'}
              prefetch={false}
              href={catalogHref(query, { card_type: key, view })}
              aria-current={filterType === key ? 'page' : undefined}
            >
              {label}
            </PanelLink>
          ))}
        </nav>
        <div className="filter-row" id="catalog-filter-fields" data-open={filtersOpen || undefined}>
          {!['hero', 'hero_skin', 'coin', 'constructed', 'anomaly', 'quest', 'reward', 'trinket'].includes(
            filterType,
          ) &&
            select(
              'tier',
              'Уровень таверны',
              'Все уровни',
              Object.fromEntries(
                Array.from(
                  { length: filterType === 'pet' || filterType === 'darkmoon_prize' ? 4 : 7 },
                  (_, i) => [String(i + 1), 'Уровень ' + (i + 1)],
                ),
              ),
            )}
          {['', 'minion', 'spell'].includes(filterType) &&
            select('creature_type', 'Тип существа', 'Все типы', data.tribes)}
          {!['hero', 'hero_skin', 'pet', 'coin', 'timewarped', 'constructed'].includes(filterType) &&
            select('pool', 'Пул таверны', 'Любой пул', { '1': 'В пуле', '0': 'Не в пуле' })}
          {['', 'minion', 'spell'].includes(filterType) &&
            select('duos', 'Режим', 'Любой режим', { '1': 'Только дуо', '0': 'Не только дуо' })}
          {filterType === 'constructed' &&
            select('constructed_format', 'Формат', 'Стандарт + Вольный', {
              standard: 'Стандартный',
              wild: 'Вольный',
            })}
          {filterType === 'hero_skin' && select('rarity', 'Редкость', 'Любая редкость', data.rarities)}
          {['hero', 'hero_skin', 'pet', 'constructed'].includes(filterType) &&
            select('media', 'Изображения', 'Все изображения', data.mediaLabels)}
          {activeCount > 0 && (
            <PanelLink
              className="link-button filter-reset"
              prefetch={false}
              replace
              scroll={false}
              href={resetHref}
            >
              <ArrowCounterClockwiseIcon size={16} aria-hidden="true" /> Сбросить
            </PanelLink>
          )}
        </div>
      </form>
      {activeCount > 0 && (
        <div className="filter-chips" role="group" aria-label="Активные фильтры">
          {data.activeFilters.map(filter => (
            <PanelLink
              key={filter.label}
              aria-label={`Убрать фильтр «${filter.label}»`}
              prefetch={false}
              replace
              scroll={false}
              href={queryHref(filter.href.split('?')[1] || '', {
                view,
                per_page: resultView === 'tiles' ? 15 : data.perPage,
              })}
            >
              {filter.label} <XIcon size={12} aria-hidden="true" />
            </PanelLink>
          ))}
        </div>
      )}
      <div className="catalog-toolbar">
        <div className="catalog-summary">
          <span>
            {data.total ? (
              <>
                Показаны{' '}
                <b>
                  {data.from}–{data.to}
                </b>{' '}
                из {data.total.toLocaleString('ru-RU')}
              </>
            ) : (
              'Нет записей'
            )}
          </span>
          <span className="catalog-update" role="status">
            {pending ? 'Обновляем результаты…' : ''}
          </span>
        </div>
        {view !== 'tiles' && (
          <div className="per-page">
            <span aria-hidden="true">На странице</span>
            <Select
              aria-label="Записей на странице"
              size="sm"
              value={String(params.get('per_page') || data.perPage)}
              onValueChange={value => go({ per_page: value })}
              options={[8, 12, 15, 25, 50, 100, 150].map(n => ({ value: String(n), label: String(n) }))}
            />
          </div>
        )}
        <SegmentedControl
          className="view-switch"
          aria-label="Вид каталога"
          size="sm"
          value={view}
          onValueChange={value => mode(catalogView(value))}
          options={[
            { value: 'tiles', label: <span>Плитки</span>, icon: <RowsIcon size={16} aria-hidden="true" /> },
            {
              value: 'grid',
              label: <span>Карточки</span>,
              icon: <SquaresFourIcon size={16} aria-hidden="true" />,
            },
            { value: 'list', label: <span>Таблица</span>, icon: <TableIcon size={16} aria-hidden="true" /> },
          ]}
        />
      </div>
      <div className="catalog-results" aria-busy={pending}>
        {!cards.length ? (
          <EmptyState
            title="Ничего не найдено"
            action={
              <PanelLink
                className="button secondary"
                prefetch={false}
                replace
                scroll={false}
                href={resetHref}
              >
                Сбросить фильтры
              </PanelLink>
            }
          >
            Измените запрос или сбросьте фильтры.
          </EmptyState>
        ) : resultView === 'tiles' ? (
          <DeckTiles cards={cards.slice(0, 15)} cardType={type} onSelect={setSelected} />
        ) : resultView === 'grid' ? (
          <div className="card-grid">
            {cards.map((card, index) => (
              <button
                className="card-tile"
                type="button"
                // biome-ignore lint/suspicious/noArrayIndexKey: imported rows can repeat a card_id
                key={card.id + '-' + index}
                onClick={() => setSelected(card.row)}
                aria-label={
                  [
                    card.name,
                    card.tier && `уровень ${card.tier}`,
                    card.attack !== '' && `атака ${card.attack}`,
                    card.health !== '' && `здоровье ${card.health}`,
                  ]
                    .filter(Boolean)
                    .join(', ') + '. Открыть детали'
                }
              >
                <span className="card-art">
                  <Artwork urls={card.images} />
                  {card.tier && (
                    <span className="card-badge card-badge--tier" title="Уровень таверны">
                      <StarIcon size={13} weight="fill" aria-hidden="true" /> {card.tier}
                    </span>
                  )}
                  {card.inPool && (
                    <span className="card-badge card-badge--pool" title="В пуле таверны">
                      <i aria-hidden="true" /> В пуле
                    </span>
                  )}
                </span>
                <span className="card-copy">
                  <strong className="card-name">{card.name}</strong>
                  <span className="card-meta">
                    <span>{card.tribe}</span>
                    <code>{card.id}</code>
                  </span>
                </span>
                <span className="card-footer">
                  <span className="card-stats">
                    {card.attack !== '' && (
                      <span className="attack" title="Атака">
                        <SwordIcon size={15} weight="fill" aria-hidden="true" /> {card.attack}
                      </span>
                    )}
                    {card.health !== '' && (
                      <span className="health" title="Здоровье">
                        <HeartIcon size={15} weight="fill" aria-hidden="true" /> {card.health}
                      </span>
                    )}
                    {card.golden && (
                      <span className="golden" title="Есть золотая версия">
                        <SparkleIcon size={15} weight="fill" aria-hidden="true" />
                      </span>
                    )}
                  </span>
                  <span className="more">
                    Открыть <ArrowRightIcon size={14} aria-hidden="true" />
                  </span>
                </span>
              </button>
            ))}
          </div>
        ) : (
          <div className="table-scroll catalog-table" role="region" aria-label="Таблица записей">
            <table>
              <thead>
                <tr>
                  <th scope="col" className="sticky-col">
                    Запись
                  </th>
                  {columns.map(column => (
                    <th key={column.key} scope="col" className={column.numeric ? 'num' : undefined}>
                      {column.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cards.map((card, i) => {
                  const nameEn = text(card.row.name_en || card.row.card_name_en || card.row.coin_name_en, '');
                  return (
                    // The name button is the keyboard path; a click anywhere on the row is a mouse shortcut.
                    <tr
                      // biome-ignore lint/suspicious/noArrayIndexKey: imported rows can repeat a card_id
                      key={card.id + '-' + i}
                      data-selected={selected === card.row || undefined}
                      onClick={event => {
                        if (!(event.target as HTMLElement).closest('a,button')) setSelected(card.row);
                      }}
                    >
                      <th scope="row" className="sticky-col">
                        <button type="button" className="row-title" onClick={() => setSelected(card.row)}>
                          <span className="row-thumb">
                            <Artwork urls={card.images} />
                          </span>
                          <span className="row-name">
                            <strong>{card.name}</strong>
                            {nameEn && nameEn !== card.name && <small lang="en">{nameEn}</small>}
                          </span>
                        </button>
                      </th>
                      {columns.map(column => (
                        <td key={column.key} className={column.numeric ? 'num' : undefined}>
                          <CellValue cell={column.cell(card.row)} />
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {data.totalPages > 1 && (
        <div className="pagination" data-pending={pending || undefined}>
          <Pagination
            aria-label="Страницы каталога"
            pageCount={data.totalPages}
            value={data.page}
            onValueChange={page => {
              if (!pending) go({ page });
            }}
            labels={{
              previous: 'Предыдущая страница',
              next: 'Следующая страница',
              page: page => `Страница ${page}`,
            }}
          />
          <span>
            Страница {data.page} из {data.totalPages}
          </span>
        </div>
      )}
      {selected && (
        // One dialog for the whole browse session: flipping cards must not close and reopen it.
        <CardInspector
          row={selected}
          cardType={type}
          tribes={data.tribes}
          apiBase={entityApiBase(type)}
          editable={normalizedCard(selected, type, data.tribes).editable}
          position={selectedIndex >= 0 ? `${selectedIndex + 1} из ${cards.length}` : undefined}
          onPrev={selectedIndex > 0 ? () => showCard(cards[selectedIndex - 1].row) : undefined}
          onNext={
            selectedIndex >= 0 && selectedIndex < cards.length - 1
              ? () => showCard(cards[selectedIndex + 1].row)
              : undefined
          }
          onClose={closeCard}
        />
      )}
    </section>
  );
}

const emptyCell = <span className="value-empty">—</span>;

function CellValue({ cell }: { cell: Cell }) {
  switch (cell.kind) {
    case 'badge':
      return <StatusBadge label={cell.value} tone={cell.tone} />;
    case 'check':
      return cell.value ? (
        <span className="cell-check">
          <CheckIcon size={16} weight="bold" aria-hidden="true" />
          <span className="sr-only">Да</span>
        </span>
      ) : (
        emptyCell
      );
    case 'time':
      return <Time value={cell.value} relative />;
    case 'pills':
      return cell.value.length ? (
        <span className="cell-pills">
          {cell.value.map(value => (
            <StatusBadge key={value} label={value} tone="info" />
          ))}
        </span>
      ) : (
        emptyCell
      );
    case 'day':
      return cell.value ? formatDay(cell.value) : emptyCell;
    case 'code':
      return cell.value ? <code>{cell.value}</code> : emptyCell;
    default:
      return cell.value || emptyCell;
  }
}
