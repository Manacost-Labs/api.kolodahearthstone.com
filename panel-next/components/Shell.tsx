'use client';
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { PanelLink, WorkspaceNavigation, useWorkspaceNavigation } from './WorkspaceNavigation';
import { catalogHref } from '@/lib/catalog-state';
import { useSearchParams } from 'next/navigation';
import { DatabaseIcon } from '@phosphor-icons/react/dist/ssr/Database';
import { ListIcon } from '@phosphor-icons/react/dist/ssr/List';
import { SignOutIcon } from '@phosphor-icons/react/dist/ssr/SignOut';
import type { User } from '@/lib/types';
import {
  readThemePreference,
  serverThemePreference,
  setThemePreference,
  subscribeThemePreference,
  themeOptions,
} from '@/lib/theme';
import { Modal } from './Modal';
type ShellProps = { user: User; logoutCsrf: string; categories: Record<string, string>; children: ReactNode };
export function Shell(props: ShellProps) {
  return (
    <WorkspaceNavigation>
      <ShellContent {...props} />
    </WorkspaceNavigation>
  );
}
function ShellContent({ user, logoutCsrf, categories, children }: ShellProps) {
  const theme = useSyncExternalStore(subscribeThemePreference, readThemePreference, serverThemePreference);
  const params = useSearchParams();
  const action = params.get('action') || 'list';
  const type = params.get('card_type') || '';
  const { pending, catalogUrl, navigate } = useWorkspaceNavigation();
  const collectionUrl = (key: string) => catalogHref(catalogUrl.split('?')[1] || '', { card_type: key });
  const [quick, setQuick] = useState(false);
  const [search, setSearch] = useState('');
  const shortcuts = [
    [catalogUrl, 'Каталог'],
    ['/?action=analytics', 'Статистика'],
    ['/?action=parsers', 'Источники'],
    ['/?action=api_tokens', 'API-токены'],
    ['/?action=wiki_terms', 'Переводы Wiki'],
    ...Object.entries(categories).map(([key, name]) => [collectionUrl(key), name]),
  ];
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearch('');
        setQuick(true);
        return;
      }
      const target = event.target;
      if (
        event.key === '/' &&
        !event.ctrlKey &&
        !event.metaKey &&
        !event.altKey &&
        !(
          target instanceof HTMLElement && target.closest('input,textarea,select,[contenteditable="true"]')
        ) &&
        !document.querySelector('dialog[open]')
      ) {
        const input = document.querySelector<HTMLInputElement>('main input[type="search"]');
        if (input) {
          event.preventDefault();
          input.focus();
          input.select();
        }
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  const catalogue = ['list', 'edit', 'new', 'wiki_terms'].includes(action);
  return (
    <>
      <header className="site-header" aria-busy={pending}>
        <div className="navigation-progress" role="status">
          {pending && (
            <>
              <span />
              Переходим…
            </>
          )}
        </div>
        <div className="header-main">
          <PanelLink className="brand" prefetch={false} href={catalogUrl} restoreCatalog>
            <span className="brand-icon">
              <DatabaseIcon size={24} />
            </span>
            <strong>HS Data</strong>
          </PanelLink>
          <nav className="primary-nav" aria-label="Основные разделы">
            {[
              [catalogUrl, 'Каталог', catalogue],
              ['/?action=analytics', 'Статистика', action === 'analytics'],
              ['/?action=parsers', 'Источники', action === 'parsers'],
              ['/?action=api_tokens', 'API-токены', action === 'api_tokens'],
            ].map(([href, name, active]) => (
              <PanelLink
                key={String(href)}
                href={String(href)}
                prefetch={false}
                restoreCatalog={name === 'Каталог'}
                aria-current={active ? 'page' : undefined}
              >
                {name}
              </PanelLink>
            ))}
          </nav>
          <div className="header-tools">
            <button
              type="button"
              className="quick-trigger"
              aria-label="Быстрый переход, Ctrl K"
              onClick={() => {
                setSearch('');
                setQuick(true);
              }}
            >
              ⌘ K
            </button>
            <span className="account-name">
              <i /> {user.login}
            </span>
            <details
              className="section-menu"
              ref={menu}
              onKeyDown={e => {
                if (e.key === 'Escape') {
                  e.currentTarget.open = false;
                  e.currentTarget.querySelector('summary')?.focus();
                }
              }}
            >
              <summary>
                <ListIcon size={18} />
                <span>Разделы</span>
              </summary>
              <div className="menu-content">
                <b>Каталоги</b>
                <div className="menu-links">
                  <PanelLink
                    prefetch={false}
                    href={collectionUrl('')}
                    aria-current={action === 'list' && !type ? 'page' : undefined}
                    onClick={() => {
                      if (menu.current) menu.current.open = false;
                    }}
                  >
                    Поля сражений
                  </PanelLink>
                  {Object.entries(categories).map(([key, name]) => (
                    <PanelLink
                      key={key}
                      prefetch={false}
                      href={collectionUrl(key)}
                      aria-current={action === 'list' && type === key ? 'page' : undefined}
                      onClick={() => {
                        if (menu.current) menu.current.open = false;
                      }}
                    >
                      {name}
                    </PanelLink>
                  ))}
                </div>
                <b>Тема</b>
                <div className="theme-options">
                  {themeOptions.map(({ key, label }) => (
                    <button
                      key={key}
                      type="button"
                      aria-pressed={theme === key}
                      onClick={() => setThemePreference(key)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <form action="/auth/logout" method="post">
                  <input type="hidden" name="csrf" value={logoutCsrf} />
                  <button type="submit" className="button secondary">
                    <SignOutIcon size={18} /> Выйти
                  </button>
                </form>
              </div>
            </details>
          </div>
        </div>
        <div className="collection-context">
          <span>{catalogue ? 'Каталог' : 'Раздел'}</span>
          <strong>
            {catalogue
              ? action === 'wiki_terms'
                ? 'Переводы Wiki'
                : categories[type] || 'Поля сражений'
              : { analytics: 'Статистика', parsers: 'Источники', api_tokens: 'API-токены' }[action] ||
                'Панель'}
          </strong>
        </div>
      </header>
      <main className="workspace">{children}</main>
      {quick && (
        <Modal title="Быстрый переход" onClose={() => setQuick(false)}>
          <form
            onSubmit={event => {
              event.preventDefault();
              const first = shortcuts.find(([, name]) => name.toLowerCase().includes(search.toLowerCase()));
              if (first) {
                setQuick(false);
                navigate(first[0], { restoreCatalog: first[0] === catalogUrl });
              }
            }}
          >
            <label className="search">
              <input
                type="search"
                aria-label="Найти раздел"
                placeholder="Название раздела…"
                value={search}
                onChange={event => setSearch(event.target.value)}
                ref={input => {
                  input?.focus();
                }}
              />
            </label>
          </form>
          <nav className="quick-links" aria-label="Результаты быстрого перехода">
            {shortcuts
              .filter(([, name]) => name.toLowerCase().includes(search.toLowerCase()))
              .map(([href, name]) => (
                <PanelLink
                  key={href}
                  prefetch={false}
                  href={href}
                  restoreCatalog={href === catalogUrl}
                  onClick={() => setQuick(false)}
                >
                  {name}
                </PanelLink>
              ))}
          </nav>
        </Modal>
      )}
    </>
  );
}
