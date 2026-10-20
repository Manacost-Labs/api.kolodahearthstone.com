'use client';

import {
  useLayoutEffect,
  useMemo,
  useRef,
  type ComponentPropsWithRef,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { ChevronLeftIcon, ChevronRightIcon, MoreHorizontalIcon } from '../icons';
import { clamp, cx, getDirection, inlineArrowStep, useControllableState } from '../primitives';
import { useLocale } from '../theme/ThemeProvider';
import { useSlidingIndicator } from './internal';
import './Pagination.css';

export type PaginationRangeItem = number | 'start-ellipsis' | 'end-ellipsis';

export interface PaginationRangeOptions {
  page: number;
  pageCount: number;
  /** Pages shown on each side of the current page. */
  siblings?: number;
  /** Pages always shown at the start and at the end. */
  boundaries?: number;
}

const range = (from: number, to: number) =>
  from > to ? [] : Array.from({ length: to - from + 1 }, (_, i) => from + i);

/**
 * Page numbers and ellipses to render. Once collapsing starts the result has a
 * constant length (2·boundaries + 2·siblings + 3), so the control never jumps
 * in width while paging; an ellipsis that would hide a single page shows the
 * page instead.
 */
export function getPaginationRange({
  page,
  pageCount,
  siblings: siblingsOption = 1,
  boundaries: boundariesOption = 1,
}: PaginationRangeOptions): PaginationRangeItem[] {
  const siblings = Math.max(0, Math.floor(siblingsOption) || 0);
  const boundaries = Math.max(0, Math.floor(boundariesOption) || 0);
  const count = Math.max(0, Math.floor(pageCount));
  const current = clamp(Math.round(page), 1, Math.max(count, 1));

  const startPages = range(1, Math.min(boundaries, count));
  const endPages = range(Math.max(count - boundaries + 1, boundaries + 1), count);

  const siblingsStart = Math.max(
    Math.min(current - siblings, count - boundaries - siblings * 2 - 1),
    boundaries + 2,
  );
  const siblingsEnd = Math.min(
    Math.max(current + siblings, boundaries + siblings * 2 + 2),
    count - boundaries - 1,
  );

  const startGap: PaginationRangeItem[] =
    siblingsStart > boundaries + 2
      ? ['start-ellipsis']
      : boundaries + 1 < count - boundaries
        ? [boundaries + 1]
        : [];
  const endGap: PaginationRangeItem[] =
    siblingsEnd < count - boundaries - 1
      ? ['end-ellipsis']
      : count - boundaries > boundaries
        ? [count - boundaries]
        : [];

  return [...startPages, ...startGap, ...range(siblingsStart, siblingsEnd), ...endGap, ...endPages];
}

export interface PaginationLabels {
  previous: string;
  next: string;
  /** Accessible name of a page button; receives the formatted page number. */
  page: (page: string) => string;
  /** Compact variant status; receives the formatted page and page count. */
  status: (page: string, pageCount: string) => ReactNode;
}

export const defaultPaginationLabels: PaginationLabels = {
  previous: 'Previous page',
  next: 'Next page',
  page: (page) => `Page ${page}`,
  status: (page, pageCount) => (
    <>
      Page <strong>{page}</strong> of {pageCount}
    </>
  ),
};

export interface PaginationProps extends Omit<ComponentPropsWithRef<'nav'>, 'defaultValue' | 'onChange'> {
  /** Total number of pages; non-finite or lower values count as 1. */
  pageCount: number;
  /** Current page, 1-based. */
  value?: number;
  defaultValue?: number;
  onValueChange?: (page: number) => void;
  /** Page buttons shown on each side of the current page before an ellipsis. */
  siblings?: number;
  /** Page buttons always shown at the start and at the end. */
  boundaries?: number;
  /** `compact` shows "Page 3 of 10" between the arrows instead of page numbers. */
  variant?: 'default' | 'compact';
  /** Height of the page buttons: 32 / 40 / 48px, like the other controls' sm / md / lg. */
  size?: 'sm' | 'md' | 'lg';
  /** Overrides for the built-in English strings. */
  labels?: Partial<PaginationLabels>;
  /** Locale for page numbers (default: the ThemeProvider's, then the browser's). */
  locale?: string;
  /** `spring`: the current-page cursor springs to the chosen page with a slight overshoot. `default`: the theme's curve. */
  animation?: 'default' | 'spring';
}

export function Pagination({
  pageCount,
  value,
  defaultValue = 1,
  onValueChange,
  siblings = 1,
  boundaries = 1,
  variant = 'default',
  size = 'sm',
  labels: labelOverrides,
  locale,
  animation = 'default',
  className,
  'aria-label': ariaLabel,
  ...rest
}: PaginationProps) {
  const [rawPage, setPage] = useControllableState({ value, defaultValue, onChange: onValueChange });
  const count = Number.isFinite(pageCount) ? Math.max(1, Math.floor(pageCount)) : 1;
  const page = clamp(Math.round(rawPage) || 1, 1, count);
  const go = (next: number) => setPage(clamp(next, 1, count));
  const listRef = useRef<HTMLUListElement>(null);
  // Set when the focused previous/next button is about to become disabled (first or last page).
  const refocus = useRef(false);

  // A disabled button drops focus to <body>; keep it in the control on the current page (or the other arrow).
  useLayoutEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    const list = listRef.current;
    const target =
      list?.querySelector<HTMLElement>('[aria-current="page"]') ??
      list?.querySelector<HTMLElement>('[data-kind="step"]:not(:disabled)');
    target?.focus();
  });

  const step = (delta: 1 | -1) => (event: { currentTarget: HTMLElement }) => {
    const next = clamp(page + delta, 1, count);
    if ((next === 1 || next === count) && document.activeElement === event.currentTarget) refocus.current = true;
    go(next);
  };

  // Arrow keys move focus between the buttons (mirrored in RTL); Enter / Space activate as usual.
  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const delta = inlineArrowStep(event.key, getDirection(event.currentTarget));
    if (delta === 0) return;
    const buttons = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));
    const next = buttons[buttons.indexOf(event.target as HTMLButtonElement) + delta];
    if (!next) return;
    event.preventDefault();
    next.focus();
  };
  const labels = { ...defaultPaginationLabels, ...labelOverrides };
  const resolvedLocale = useLocale(locale);
  const numberFormat = useMemo(() => new Intl.NumberFormat(resolvedLocale), [resolvedLocale]);
  const format = (n: number) => numberFormat.format(n);
  // The current page's fill slides between page buttons; until it is measured (SSR, first paint) the
  // current button paints its own fill.
  const cursor = useSlidingIndicator(listRef, variant === 'compact' ? null : page, ':scope > li > [aria-current="page"]');

  return (
    <nav
      {...rest}
      // Several on one page need distinct names: pass `aria-label`, or `aria-labelledby` to name it by a heading.
      aria-label={ariaLabel ?? (rest['aria-labelledby'] ? undefined : 'Pagination')}
      className={cx('ui-pagination', className)}
      data-variant={variant}
      data-size={size}
      data-motion={animation === 'spring' ? 'spring' : undefined}
    >
      <ul ref={listRef} className="ui-pagination__list" data-cursor={cursor ? '' : undefined} onKeyDown={onKeyDown}>
        {cursor && (
          <li
            className="ui-pagination__cursor"
            aria-hidden="true"
            style={{
              width: cursor.width,
              height: cursor.height,
              transform: `translate(${cursor.left}px, ${cursor.top}px)`,
              transition: cursor.instant ? 'none' : undefined,
            }}
          />
        )}
        <li>
          <button
            type="button"
            className="ui-pagination__item"
            data-kind="step"
            aria-label={labels.previous}
            disabled={page <= 1}
            onClick={step(-1)}
          >
            <ChevronLeftIcon />
          </button>
        </li>
        {variant === 'compact' ? (
          <li className="ui-pagination__status" aria-live="polite">
            {labels.status(format(page), format(count))}
          </li>
        ) : (
          getPaginationRange({ page, pageCount: count, siblings, boundaries }).map((item) =>
            typeof item === 'number' ? (
              <li key={item}>
                <button
                  type="button"
                  className="ui-pagination__item"
                  aria-label={labels.page(format(item))}
                  aria-current={item === page ? 'page' : undefined}
                  onClick={() => go(item)}
                >
                  {format(item)}
                </button>
              </li>
            ) : (
              <li key={item} className="ui-pagination__ellipsis" aria-hidden="true">
                <MoreHorizontalIcon />
              </li>
            ),
          )
        )}
        <li>
          <button
            type="button"
            className="ui-pagination__item"
            data-kind="step"
            aria-label={labels.next}
            disabled={page >= count}
            onClick={step(1)}
          >
            <ChevronRightIcon />
          </button>
        </li>
      </ul>
    </nav>
  );
}
