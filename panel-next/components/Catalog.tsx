'use client';
import { useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { ArrowCounterClockwiseIcon } from '@phosphor-icons/react/dist/ssr/ArrowCounterClockwise';
import { SlidersHorizontalIcon } from '@phosphor-icons/react/dist/ssr/SlidersHorizontal';
import { PanelLink } from './WorkspaceNavigation';
import { useCatalogQuery } from './useCatalogQuery';
import { catalogHref, catalogView } from '@/lib/catalog-state';
import { countLabel } from '@/lib/format';
import { SquaresFourIcon } from '@phosphor-icons/react/dist/ssr/SquaresFour';
import { RowsIcon } from '@phosphor-icons/react/dist/ssr/Rows';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { ArrowRightIcon } from '@phosphor-icons/react/dist/ssr/ArrowRight';
import { CaretLeftIcon } from '@phosphor-icons/react/dist/ssr/CaretLeft';
import { CaretRightIcon } from '@phosphor-icons/react/dist/ssr/CaretRight';
import { HeartIcon } from '@phosphor-icons/react/dist/ssr/Heart';
import { SparkleIcon } from '@phosphor-icons/react/dist/ssr/Sparkle';
import { StarIcon } from '@phosphor-icons/react/dist/ssr/Star';
import { SwordIcon } from '@phosphor-icons/react/dist/ssr/Sword';
import { TableIcon } from '@phosphor-icons/react/dist/ssr/Table';
import { XIcon } from '@phosphor-icons/react/dist/ssr/X';
import { cardKey, matchesCard } from '@/lib/card-detail';
import { normalizedCard, queryHref, entityApiBase } from '@/lib/model';
import type { PageData, Row } from '@/lib/types';
import { CardInspector } from './CardInspector';
import { Artwork } from './Artwork';
import { DeckTiles } from './DeckTiles';
import { EmptyState } from './ui';

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
    <label className="filter-field">
      <span>{label}</span>
      <select name={name} value={params.get(name) || ''} onChange={e => go({ [name]: e.target.value })}>
        <option value="">{anyLabel}</option>
        {Object.entries(options).map(([key, optionLabel]) => (
          <option key={key} value={key}>
            {optionLabel}
          </option>
        ))}
      </select>
    </label>
  );
  const filterKeys = ['tier', 'creature_type', 'pool', 'duos', 'constructed_format', 'rarity', 'media'];
  const activeCount = Math.max(data.activeFilters.length, filterKeys.filter(key => params.get(key)).length);
  const cards = data.records.map(row => normalizedCard(row, type, data.tribes));
  const selectedIndex = cardParam ? cards.findIndex(card => matchesCard(card.row, cardParam)) : -1;
  const selected =
    selectedIndex >= 0 ? cards[selectedIndex].row : focus && matchesCard(focus, cardParam) ? focus : null;
  return (
    <section className="catalog">
      <header className="page-head">
        <div>
          <span className="eyebrow">База Hearthstone</span>
          <h1>{data.title}</h1>
          <p>
            {data.total
              ? `${countLabel(data.total, { one: 'запись', few: 'записи', many: 'записей' })} в разделе`
              : 'Записей пока нет'}
          </p>
        </div>
        {['', 'minion', 'spell'].includes(type) && (
          <PanelLink className="button secondary" href="/?action=new" prefetch={false}>
            Добавить карту
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
            <MagnifyingGlassIcon size={20} aria-hidden="true" />
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
            <SlidersHorizontalIcon size={18} aria-hidden="true" />
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
        <label className="per-page">
          <span>На странице</span>
          <select
            name="per_page"
            value={params.get('per_page') || data.perPage}
            disabled={view === 'tiles'}
            onChange={e => go({ per_page: e.target.value })}
          >
            {[8, 12, 15, 25, 50, 100, 150].map(n => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
        <div className="view-switch" role="group" aria-label="Вид каталога">
          <button type="button" aria-pressed={view === 'tiles'} onClick={() => mode('tiles')}>
            <RowsIcon size={18} aria-hidden="true" /> <span>Плитки</span>
          </button>
          <button type="button" aria-pressed={view === 'grid'} onClick={() => mode('grid')}>
            <SquaresFourIcon size={18} aria-hidden="true" /> <span>Карточки</span>
          </button>
          <button type="button" aria-pressed={view === 'list'} onClick={() => mode('list')}>
            <TableIcon size={18} aria-hidden="true" /> <span>Таблица</span>
          </button>
        </div>
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
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {['Карта', 'Card ID', 'Тип', 'Уровень', 'Атака', 'Здоровье', 'Пул', 'Просмотр'].map(h => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cards.map((card, i) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: imported rows can repeat a card_id
                  <tr key={card.id + '-' + i}>
                    <td>
                      <strong>{card.name}</strong>
                    </td>
                    <td>
                      <code>{card.id}</code>
                    </td>
                    <td>{card.tribe}</td>
                    <td>{card.tier || '—'}</td>
                    <td>{card.attack || '—'}</td>
                    <td>{card.health || '—'}</td>
                    <td>{card.inPool ? 'В пуле' : '—'}</td>
                    <td>
                      <button type="button" className="link-button" onClick={() => setSelected(card.row)}>
                        Подробнее
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <nav className="pagination" aria-label="Страницы каталога">
        <button
          type="button"
          className="page-link"
          disabled={pending || data.page === 1}
          onClick={() => go({ page: data.page - 1 })}
        >
          <CaretLeftIcon size={16} aria-hidden="true" /> Назад
        </button>
        {Array.from(
          { length: Math.min(5, data.totalPages) },
          (_, i) => Math.max(1, Math.min(data.page - 2, data.totalPages - 4)) + i,
        ).map(page => (
          <button
            key={page}
            type="button"
            className="page-link"
            disabled={pending}
            aria-current={page === data.page ? 'page' : undefined}
            onClick={() => go({ page })}
          >
            {page}
          </button>
        ))}
        <button
          type="button"
          className="page-link"
          disabled={pending || data.page === data.totalPages}
          onClick={() => go({ page: data.page + 1 })}
        >
          Вперёд <CaretRightIcon size={16} aria-hidden="true" />
        </button>
        <span>
          Страница {data.page} из {data.totalPages}
        </span>
      </nav>
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
