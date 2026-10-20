'use client';

import { useEffect, useId, useLayoutEffect, useRef, type ComponentPropsWithRef, type KeyboardEvent, type ReactNode } from 'react';
import { animateSpring, cx, getDirection, useControllableState } from '../primitives';
import { Badge } from '../basic/Badge';
import { revealInList, rovingTarget, useScrollEdges, useSlidingIndicator } from './internal';
import '../primitives/motion.css';
import './Tabs.css';

export interface TabItem {
  value: string;
  label: ReactNode;
  icon?: ReactNode;
  /** Count or short text shown after the label. */
  badge?: ReactNode;
  disabled?: boolean;
  /** Panel content. Tabs without content act as a plain switcher (no tabpanel). */
  content?: ReactNode;
}

export interface TabsProps extends Omit<ComponentPropsWithRef<'div'>, 'defaultValue' | 'onChange' | 'children'> {
  /** Tabs in order, each with optional panel content. */
  items: TabItem[];
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** `underline`: a bar slides along the baseline; `pills`: a filled shape slides behind the active tab. */
  variant?: 'underline' | 'pills';
  /** Accessible name of the tab list. */
  label?: string;
  /**
   * `spring`: the indicator springs between tabs, the new panel slides in from the side of the tab it
   * came from and the panel area eases to the new height. `default`: the theme's indicator, instant panels.
   */
  animation?: 'default' | 'spring';
}

export function Tabs({
  items,
  value,
  defaultValue,
  onValueChange,
  variant = 'underline',
  label,
  animation = 'default',
  className,
  ...rest
}: TabsProps) {
  const [current, setCurrent] = useControllableState({
    value,
    defaultValue: defaultValue ?? items.find((item) => !item.disabled)?.value ?? '',
    onChange: onValueChange,
  });
  const baseId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const indicator = useSlidingIndicator(listRef, current);
  useScrollEdges(listRef);

  // Keeps the selected tab visible when the value changes from outside (or starts) past the scroll edge.
  useLayoutEffect(() => {
    const list = listRef.current;
    const tab = list?.querySelector<HTMLElement>(':scope > [data-active="true"]');
    if (list && tab) revealInList(list, tab);
  }, [current]);

  const isDisabled = (index: number) => !!items[index]?.disabled;
  const selectedIndex = items.findIndex((item) => item.value === current);
  const spring = animation === 'spring';
  const panelsRef = useRef<HTMLDivElement>(null);
  const shown = useRef({ index: selectedIndex, height: 0 });

  // Spring: remember the panel area's height as it changes (content loading, resizing), so a switch can
  // ease from the height that was really on screen.
  useEffect(() => {
    const panels = panelsRef.current;
    if (!spring || !panels || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      if (!panels.getAnimations().length) shown.current.height = panels.offsetHeight;
    });
    observer.observe(panels);
    return () => observer.disconnect();
  }, [spring]);

  // Spring: the new panel slides in from the side of the previous tab while the area eases to its height.
  useLayoutEffect(() => {
    const panels = panelsRef.current;
    const previous = shown.current;
    shown.current = { index: selectedIndex, height: panels?.offsetHeight ?? 0 };
    if (!spring || !panels || previous.index < 0 || previous.index === selectedIndex || selectedIndex < 0) return;
    const panel = document.getElementById(panelId(selectedIndex));
    if (!panel) return;
    const forward = selectedIndex > previous.index ? 1 : -1;
    const side = forward * (getDirection(panels) === 'rtl' ? -1 : 1);
    animateSpring(panel, { transform: [`translateX(${side * 24}px)`, 'none'], opacity: [0, 1] }, 'snappy');
    const height = panels.offsetHeight;
    if (previous.height && previous.height !== height) {
      panels.setAttribute('data-animating', '');
      const growth = animateSpring(panels, { height: [`${previous.height}px`, `${height}px`] }, 'smooth');
      const done = () => panels.removeAttribute('data-animating');
      if (growth) growth.onfinish = growth.oncancel = done;
      else done();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- panel ids are stable for the component's life
  }, [selectedIndex, spring]);
  // Keep the list reachable by Tab even when the value matches no tab.
  const focusableIndex = selectedIndex >= 0 ? selectedIndex : items.findIndex((item) => !item.disabled);
  const tabId = (index: number) => `${baseId}-tab-${index}`;
  const panelId = (index: number) => `${baseId}-panel-${index}`;

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const from = items.findIndex((item) => item.value === (event.target as HTMLElement).dataset.value);
    if (from < 0) return;
    const next = rovingTarget(event.key, items.length, from, isDisabled, 'horizontal', getDirection(event.currentTarget));
    if (next === null) return;
    event.preventDefault();
    // Automatic activation: focusing a tab selects it.
    document.getElementById(tabId(next))?.focus();
    setCurrent(items[next].value);
  };

  return (
    <div {...rest} className={cx('ui-tabs', className)} data-variant={variant} data-motion={spring ? 'spring' : undefined}>
      <div
        ref={listRef}
        role="tablist"
        aria-label={label}
        aria-orientation="horizontal"
        className="ui-tabs__list"
        onKeyDown={onKeyDown}
      >
        {items.map((item, index) => {
          const selected = index === selectedIndex;
          return (
            <button
              key={item.value}
              type="button"
              role="tab"
              id={tabId(index)}
              aria-selected={selected}
              aria-controls={item.content !== undefined ? panelId(index) : undefined}
              tabIndex={index === focusableIndex ? 0 : -1}
              disabled={item.disabled}
              data-value={item.value}
              data-active={selected || undefined}
              className="ui-tabs__tab"
              onClick={() => setCurrent(item.value)}
            >
              {item.icon && <span className="ui-tabs__icon">{item.icon}</span>}
              <span className="ui-tabs__label">{item.label}</span>
              {item.badge != null && (
                <Badge size="sm" className="ui-tabs__badge">
                  {item.badge}
                </Badge>
              )}
            </button>
          );
        })}
        {indicator && (
          <span
            className="ui-tabs__indicator"
            aria-hidden="true"
            style={{ width: indicator.width, transform: `translateX(${indicator.left}px)`, transition: indicator.instant ? 'none' : undefined }}
          />
        )}
      </div>
      {spring ? (
        <div ref={panelsRef} className="ui-tabs__panels">
          {renderPanels()}
        </div>
      ) : (
        renderPanels()
      )}
    </div>
  );

  function renderPanels() {
    return items.map(
      (item, index) =>
        item.content !== undefined && (
          <div
            key={item.value}
            role="tabpanel"
            id={panelId(index)}
            aria-labelledby={tabId(index)}
            hidden={index !== selectedIndex}
            tabIndex={0}
            className="ui-tabs__panel"
          >
            {item.content}
          </div>
        ),
    );
  }
}
