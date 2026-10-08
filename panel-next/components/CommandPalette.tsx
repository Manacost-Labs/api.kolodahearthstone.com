'use client';
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import { ArrowElbowDownLeftIcon } from '@phosphor-icons/react/dist/ssr/ArrowElbowDownLeft';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { ChartBarIcon } from '@phosphor-icons/react/dist/ssr/ChartBar';
import { DatabaseIcon } from '@phosphor-icons/react/dist/ssr/Database';
import { KeyIcon } from '@phosphor-icons/react/dist/ssr/Key';
import { PlugsConnectedIcon } from '@phosphor-icons/react/dist/ssr/PlugsConnected';
import { SquaresFourIcon } from '@phosphor-icons/react/dist/ssr/SquaresFour';
import { TranslateIcon } from '@phosphor-icons/react/dist/ssr/Translate';
import { cardHref, cardKey } from '@/lib/card-detail';
import { apiPath } from '@/lib/client';
import { normalizedCard } from '@/lib/model';
import type { Row } from '@/lib/types';
import { Artwork } from './Artwork';
import { Modal } from './Modal';
import { PanelLink } from './WorkspaceNavigation';

type Item = { key: string; label: string; hint?: string; href: string; images?: string[]; catalog?: boolean };
type Search = { status: 'idle' | 'loading' | 'done' | 'error'; items: Item[] };

const sectionIcons = [
  ['action=analytics', ChartBarIcon],
  ['action=parsers', PlugsConnectedIcon],
  ['action=api_tokens', KeyIcon],
  ['action=wiki_terms', TranslateIcon],
] as const;
function SectionIcon({ item }: { item: Item }) {
  const Icon = item.catalog
    ? DatabaseIcon
    : (sectionIcons.find(([needle]) => item.href.includes(needle))?.[1] ?? SquaresFourIcon);
  return <Icon size={16} aria-hidden="true" />;
}

const normalize = (value: string) => value.toLowerCase().replace(/ё/g, 'е').trim();
const MIN_QUERY = 2;

/** Searches the current category through the PHP bridge; stale requests are aborted. */
function useCardSearch(query: string, cardType: string): Search {
  const [search, setSearch] = useState<Search>({ status: 'idle', items: [] });
  useEffect(() => {
    const q = query.trim();
    if (q.length < MIN_QUERY) {
      setSearch({ status: 'idle', items: [] });
      return;
    }
    setSearch(previous => ({ status: 'loading', items: previous.items }));
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ view: 'panel', action: 'list', q, per_page: '8' });
        if (cardType) params.set('card_type', cardType);
        const response = await fetch(`${apiPath}?${params}`, {
          cache: 'no-store',
          signal: controller.signal,
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) throw new Error('search failed');
        const tribes = (payload.tribes ?? {}) as Record<string, string>;
        const items = ((payload.records ?? []) as Row[]).map(row => {
          const card = normalizedCard(row, cardType, tribes);
          const key = cardKey(row);
          return {
            key: 'card:' + key,
            label: card.name,
            hint: [card.tribe, card.id].filter(Boolean).join(' · '),
            href: cardHref(cardType, key),
            images: card.images,
          };
        });
        setSearch({ status: 'done', items });
      } catch {
        if (!controller.signal.aborted) setSearch({ status: 'error', items: [] });
      }
    }, 180);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, cardType]);
  return search;
}

export function CommandPalette({
  sections,
  catalogUrl,
  cardType,
  categoryName,
  onNavigate,
  onClose,
}: {
  sections: [string, string][];
  catalogUrl: string;
  cardType: string;
  categoryName: string;
  onNavigate: (href: string, restoreCatalog: boolean) => void;
  onClose: () => void;
}) {
  const id = useId();
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const list = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const cards = useCardSearch(query, cardType);
  const needle = normalize(query);
  const sectionItems: Item[] = sections
    .filter(([, name]) => normalize(name).includes(needle))
    .map(([href, name]) => ({ key: 'section:' + href, label: name, href, catalog: href === catalogUrl }));
  const items = [...sectionItems, ...cards.items];
  const current = Math.min(active, Math.max(0, items.length - 1));
  const optionId = (index: number) => `${id}-option-${index}`;

  // Modal focuses its close button when it opens; the search field takes focus right after.
  useEffect(() => {
    const frame = requestAnimationFrame(() => input.current?.focus());
    return () => cancelAnimationFrame(frame);
  }, []);
  // Keep the highlighted option in view while moving with the arrows.
  useEffect(() => {
    list.current?.querySelector(`[id="${optionId(current)}"]`)?.scrollIntoView({ block: 'nearest' });
  });

  const open = (item: Item | undefined) => {
    if (!item) return;
    onClose();
    onNavigate(item.href, Boolean(item.catalog));
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    const last = items.length - 1;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (last < 0) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current + step + items.length) % items.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      open(items[current]);
    }
  };

  const status =
    query.trim().length < MIN_QUERY
      ? 'Введите название или ID карты, чтобы искать в разделе.'
      : cards.status === 'loading'
        ? 'Ищем карты…'
        : cards.status === 'error'
          ? 'Не удалось выполнить поиск карт.'
          : cards.items.length
            ? `Найдено карт: ${cards.items.length}`
            : 'Карт с таким названием нет.';

  const option = (item: Item, index: number) => (
    <PanelLink
      key={item.key}
      id={optionId(index)}
      role="option"
      tabIndex={-1}
      aria-selected={index === current}
      className="palette-option"
      prefetch={false}
      href={item.href}
      restoreCatalog={item.catalog}
      onMouseMove={() => {
        if (index !== current) setActive(index);
      }}
      onClick={onClose}
    >
      {item.images ? (
        <span className="palette-thumb">
          <Artwork urls={item.images} />
        </span>
      ) : (
        <span className="palette-icon">
          <SectionIcon item={item} />
        </span>
      )}
      <span className="palette-label">
        <strong>{item.label}</strong>
        {item.hint && <small>{item.hint}</small>}
      </span>
      <ArrowElbowDownLeftIcon className="palette-enter" size={14} aria-hidden="true" />
    </PanelLink>
  );

  return (
    <Modal title="Быстрый переход" onClose={onClose}>
      <div className="palette">
        <label className="palette-search">
          <MagnifyingGlassIcon size={18} aria-hidden="true" />
          <input
            type="search"
            role="combobox"
            aria-label="Найти раздел или карту"
            aria-expanded={items.length > 0}
            aria-controls={`${id}-list`}
            aria-activedescendant={items.length ? optionId(current) : undefined}
            aria-autocomplete="list"
            placeholder="Раздел, название карты или ID…"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={event => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            ref={input}
          />
          {cards.status === 'loading' && <span className="palette-spinner" aria-hidden="true" />}
        </label>
        <div className="palette-list" id={`${id}-list`} role="listbox" aria-label="Результаты" ref={list}>
          {sectionItems.length > 0 && (
            <div role="group" aria-labelledby={`${id}-sections`}>
              <div className="palette-group" id={`${id}-sections`} role="presentation">
                Разделы
              </div>
              {sectionItems.map((item, index) => option(item, index))}
            </div>
          )}
          {cards.items.length > 0 && (
            <div role="group" aria-labelledby={`${id}-cards`}>
              <div className="palette-group" id={`${id}-cards`} role="presentation">
                Карты · {categoryName}
              </div>
              {cards.items.map((item, index) => option(item, sectionItems.length + index))}
            </div>
          )}
        </div>
        <p className="palette-status" role="status">
          {status}
        </p>
        <footer className="palette-keys" aria-hidden="true">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> выбрать
          </span>
          <span>
            <kbd>Enter</kbd> открыть
          </span>
          <span>
            <kbd>Esc</kbd> закрыть
          </span>
        </footer>
      </div>
    </Modal>
  );
}
