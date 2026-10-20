'use client';
import { useEffect, useRef, useState, useSyncExternalStore, type MouseEvent, type ReactNode } from 'react';
import { PanelLink, WorkspaceNavigation, useWorkspaceNavigation } from './WorkspaceNavigation';
import { catalogHref } from '@/lib/catalog-state';
import { useSearchParams } from 'next/navigation';
import { CaretRightIcon } from '@phosphor-icons/react/dist/ssr/CaretRight';
import { CaretUpDownIcon } from '@phosphor-icons/react/dist/ssr/CaretUpDown';
import { ChartBarIcon } from '@phosphor-icons/react/dist/ssr/ChartBar';
import { DatabaseIcon } from '@phosphor-icons/react/dist/ssr/Database';
import { KeyIcon } from '@phosphor-icons/react/dist/ssr/Key';
import { MagnifyingGlassIcon } from '@phosphor-icons/react/dist/ssr/MagnifyingGlass';
import { PlugsConnectedIcon } from '@phosphor-icons/react/dist/ssr/PlugsConnected';
import { SquaresFourIcon } from '@phosphor-icons/react/dist/ssr/SquaresFour';
import { TranslateIcon } from '@phosphor-icons/react/dist/ssr/Translate';
import type { User } from '@/lib/types';
import {
  readThemeMode,
  readThemePreference,
  serverThemeMode,
  serverThemePreference,
  setThemePreference,
  subscribeThemePreference,
  themeOptions,
  isThemePreference,
} from '@/lib/theme';
import { ThemeProvider } from './varylo-ui/theme/ThemeProvider';
import { AppShell, AppShellSidebarToggle, type AppShellState } from './varylo-ui/layout/AppShell';
import { Sidebar, type SidebarSection } from './varylo-ui/navigation/Sidebar';
import { DropdownMenu } from './varylo-ui/navigation/DropdownMenu';
import { CommandPalette } from './CommandPalette';

type ShellProps = { user: User; logoutCsrf: string; categories: Record<string, string>; children: ReactNode };

const shellLabels = {
  skipToContent: 'Перейти к содержимому',
  navigation: 'Навигация',
  aside: 'Детали',
  expandSidebar: 'Развернуть меню',
  collapseSidebar: 'Свернуть меню',
  openNavigation: 'Открыть меню',
};
const sectionNames: Record<string, string> = {
  analytics: 'Статистика',
  parsers: 'Источники',
  api_tokens: 'API-токены',
  wiki_terms: 'Переводы Wiki',
  edit: 'Редактор карты',
  new: 'Новая карта',
};

export function Shell(props: ShellProps) {
  return (
    <WorkspaceNavigation>
      <ShellContent {...props} />
    </WorkspaceNavigation>
  );
}

function ShellContent({ user, logoutCsrf, categories, children }: ShellProps) {
  const mode = useSyncExternalStore(subscribeThemePreference, readThemeMode, serverThemeMode);
  const params = useSearchParams();
  const action = params.get('action') || 'list';
  const type = params.get('card_type') || '';
  const { pending, catalogUrl, navigate } = useWorkspaceNavigation();
  const collectionUrl = (key: string) => catalogHref(catalogUrl.split('?')[1] || '', { card_type: key });
  const catalogType = new URLSearchParams(catalogUrl.split('?')[1] || '').get('card_type') || '';
  // The category being browsed links back to its exact page, filters and scroll position.
  const categoryUrl = (key: string) => (key === catalogType ? catalogUrl : collectionUrl(key));
  const [quick, setQuick] = useState(false);
  const shortcuts: [string, string][] = [
    [catalogUrl, 'Каталог'],
    ['/?action=analytics', 'Статистика'],
    ['/?action=parsers', 'Источники'],
    ['/?action=api_tokens', 'API-токены'],
    ['/?action=wiki_terms', 'Переводы Wiki'],
    ...Object.entries(categories).map(([key, name]): [string, string] => [collectionUrl(key), name]),
  ];
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
        const input = document.querySelector<HTMLInputElement>('#panel-main input[type="search"]');
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
  const categoryName = categories[type] || 'Поля сражений';
  const active = action === 'list' ? 'cat:' + type : action === 'wiki_terms' ? 'wiki_terms' : action;
  const sections: SidebarSection[] = [
    {
      items: [
        {
          id: 'catalog',
          label: 'Каталог',
          icon: <DatabaseIcon size={16} />,
          defaultOpen: true,
          items: [['', 'Поля сражений'] as const, ...Object.entries(categories)].map(([key, name]) => ({
            id: 'cat:' + key,
            label: name,
            href: categoryUrl(key),
          })),
        },
        {
          id: 'analytics',
          label: 'Статистика',
          icon: <ChartBarIcon size={16} />,
          href: '/?action=analytics',
        },
        {
          id: 'parsers',
          label: 'Источники',
          icon: <PlugsConnectedIcon size={16} />,
          href: '/?action=parsers',
        },
      ],
    },
    {
      label: 'Управление',
      items: [
        { id: 'api_tokens', label: 'API-токены', icon: <KeyIcon size={16} />, href: '/?action=api_tokens' },
        {
          id: 'wiki_terms',
          label: 'Переводы Wiki',
          icon: <TranslateIcon size={16} />,
          href: '/?action=wiki_terms',
        },
      ],
    },
  ];
  // Sidebar items are plain links; plain clicks go through the workspace router instead of a reload.
  const followLink = (event: MouseEvent, shell: AppShellState) => {
    const link = (event.target as HTMLElement).closest<HTMLAnchorElement>('a[href]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return;
    const href = link.getAttribute('href') || '/';
    if (!href.startsWith('/')) return;
    event.preventDefault();
    if (shell.mobile) shell.setSidebarOpen(false);
    navigate(href, { restoreCatalog: href === catalogUrl });
  };
  return (
    <ThemeProvider theme="vercel" mode={mode} locale="ru-RU">
      <AppShell
        className="panel-shell"
        mainId="panel-main"
        mobileBreakpoint={900}
        labels={shellLabels}
        sidebar={shell => (
          // biome-ignore lint/a11y/noStaticElementInteractions: delegates clicks of the links inside
          // biome-ignore lint/a11y/useKeyWithClickEvents: links keep their own keyboard activation
          <div className="panel-sidebar" onClick={event => followLink(event, shell)}>
            <Sidebar
              label="Разделы панели"
              sections={sections}
              value={active}
              collapsed={shell.sidebarCollapsed}
              header={
                <PanelLink className="brand" prefetch={false} href={catalogUrl} restoreCatalog>
                  <span className="brand-icon" aria-hidden="true">
                    <SquaresFourIcon size={16} weight="fill" />
                  </span>
                  {!shell.sidebarCollapsed && (
                    <span className="brand-name">
                      <strong>HS Data</strong>
                      <small>Koloda Hearthstone</small>
                    </span>
                  )}
                </PanelLink>
              }
              footer={<AccountMenu user={user} logoutCsrf={logoutCsrf} compact={shell.sidebarCollapsed} />}
            />
          </div>
        )}
        header={
          <div className="topbar" aria-busy={pending}>
            <div className="navigation-progress" role="status">
              {pending && (
                <>
                  <span />
                  Переходим…
                </>
              )}
            </div>
            <AppShellSidebarToggle variant="ghost" size="sm" />
            <nav className="topbar-crumbs" aria-label="Расположение">
              <span>{catalogue ? 'Каталог' : 'Панель'}</span>
              <CaretRightIcon size={12} aria-hidden="true" />
              <strong aria-current="page">
                {action === 'list' ? categoryName : sectionNames[action] || 'Панель'}
              </strong>
            </nav>
            <button
              type="button"
              className="quick-trigger"
              aria-keyshortcuts="Control+K Meta+K"
              aria-label="Быстрый переход и поиск карт"
              onClick={() => setQuick(true)}
            >
              <MagnifyingGlassIcon size={16} aria-hidden="true" />
              <span>Поиск…</span>
              <kbd>Ctrl K</kbd>
            </button>
          </div>
        }
      >
        <div className="workspace">{children}</div>
      </AppShell>
      {quick && (
        <CommandPalette
          sections={shortcuts}
          catalogUrl={catalogUrl}
          cardType={type}
          categoryName={categoryName}
          onNavigate={(href, restoreCatalog) => navigate(href, { restoreCatalog })}
          onClose={() => setQuick(false)}
        />
      )}
    </ThemeProvider>
  );
}

function AccountMenu({ user, logoutCsrf, compact }: { user: User; logoutCsrf: string; compact: boolean }) {
  const preference = useSyncExternalStore(
    subscribeThemePreference,
    readThemePreference,
    serverThemePreference,
  );
  const logout = useRef<HTMLFormElement>(null);
  return (
    <>
      <DropdownMenu
        placement={compact ? 'right-end' : 'top-start'}
        items={[
          { type: 'label', label: user.login },
          {
            type: 'radio',
            label: 'Тема',
            value: preference,
            onValueChange: value => {
              if (isThemePreference(value)) setThemePreference(value);
            },
            options: themeOptions.map(({ key, label }) => ({ value: key, label })),
          },
          { type: 'separator' },
          { label: 'Выйти', danger: true, onSelect: () => logout.current?.requestSubmit() },
        ]}
        trigger={
          <button type="button" className="account-trigger" aria-label={`Аккаунт ${user.login}`}>
            <span className="account-avatar" aria-hidden="true">
              {user.login.slice(0, 1).toUpperCase()}
            </span>
            {!compact && (
              <>
                <span className="account-name">
                  <strong>{user.login}</strong>
                  <small>GitHub · администратор</small>
                </span>
                <CaretUpDownIcon size={14} aria-hidden="true" />
              </>
            )}
          </button>
        }
      />
      <form ref={logout} action="/auth/logout" method="post" hidden>
        <input type="hidden" name="csrf" value={logoutCsrf} />
      </form>
    </>
  );
}
