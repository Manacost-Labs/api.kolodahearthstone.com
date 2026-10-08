'use client';
import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from 'react';
import { PanelLink, WorkspaceNavigation, useWorkspaceNavigation } from './WorkspaceNavigation';
import { catalogHref } from '@/lib/catalog-state';
import { useSearchParams } from 'next/navigation';
import { DatabaseIcon } from '@phosphor-icons/react/dist/ssr/Database';
import { ListIcon } from '@phosphor-icons/react/dist/ssr/List';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { SignOutIcon } from '@phosphor-icons/react/dist/ssr/SignOut';
import type { User } from '@/lib/types';
import {
  readThemePreference,
  serverThemePreference,
  setThemePreference,
  subscribeThemePreference,
  themeOptions,
} from '@/lib/theme';
import { CommandPalette } from './CommandPalette';
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
  const shortcuts: [string, string][] = [
    [catalogUrl, 'Каталог'],
    ['/?action=analytics', 'Статистика'],
    ['/?action=parsers', 'Источники'],
    ['/?action=api_tokens', 'API-токены'],
    ['/?action=wiki_terms', 'Переводы Wiki'],
    ...Object.entries(categories).map(([key, name]): [string, string] => [collectionUrl(key), name]),
  ];
  const menu = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
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
              aria-keyshortcuts="Control+K Meta+K"
              onClick={() => setQuick(true)}
            >
              <MagnifyingGlassIcon size={16} aria-hidden="true" />
              <span>Поиск</span>
              <kbd>Ctrl K</kbd>
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
        <CommandPalette
          sections={shortcuts}
          catalogUrl={catalogUrl}
          cardType={type}
          categoryName={categories[type] || 'Поля сражений'}
          onNavigate={(href, restoreCatalog) => navigate(href, { restoreCatalog })}
          onClose={() => setQuick(false)}
        />
      )}
    </>
  );
}
