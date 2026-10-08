'use client';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  useTransition,
  type ComponentProps,
  type ReactNode,
} from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';

type Options = { replace?: boolean; scroll?: boolean; restoreCatalog?: boolean };
type Navigation = {
  pending: boolean;
  catalogUrl: string;
  navigate: (href: string, options?: Options) => void;
  beforeNavigate: (callback: () => void) => () => void;
};
const Context = createContext<Navigation | null>(null);

export function WorkspaceNavigation({ children }: { children: ReactNode }) {
  const router = useRouter();
  const params = useSearchParams();
  const query = params.toString();
  const isCatalog = !params.get('action') || params.get('action') === 'list';
  const [pending, startTransition] = useTransition();
  const [catalogUrl, setCatalogUrl] = useState(isCatalog ? '/' + (query ? '?' + query : '') : '/');
  const catalogScroll = useRef(0);
  const restore = useRef<{ href: string; y: number } | null>(null);
  const callbacks = useRef(new Set<() => void>());
  const beforeNavigate = useCallback((callback: () => void) => {
    callbacks.current.add(callback);
    return () => {
      callbacks.current.delete(callback);
    };
  }, []);
  useEffect(() => {
    if (pending) return;
    const href = '/' + (query ? '?' + query : '');
    // Remember the last settled catalogue URL; it only changes after a finished transition.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (isCatalog) setCatalogUrl(href);
    const target = restore.current;
    restore.current = null;
    if (target?.href === href) {
      const frame = requestAnimationFrame(() => window.scrollTo({ top: target.y, behavior: 'instant' }));
      return () => cancelAnimationFrame(frame);
    }
  }, [query, isCatalog, pending]);
  const navigate = useCallback(
    (href: string, options: Options = {}) => {
      for (const callback of callbacks.current) callback();
      if (isCatalog && !pending) catalogScroll.current = window.scrollY;
      restore.current = options.restoreCatalog ? { href, y: catalogScroll.current } : null;
      startTransition(() => {
        const settings = { scroll: options.restoreCatalog ? false : (options.scroll ?? true) };
        if (options.replace) router.replace(href, settings);
        else router.push(href, settings);
      });
    },
    [router, isCatalog, pending],
  );
  return (
    <Context.Provider value={{ pending, catalogUrl, navigate, beforeNavigate }}>{children}</Context.Provider>
  );
}

export function useWorkspaceNavigation() {
  const value = useContext(Context);
  if (!value) throw new Error('Workspace navigation is unavailable.');
  return value;
}

export function PanelLink({
  href,
  replace,
  scroll,
  restoreCatalog,
  ...props
}: Omit<ComponentProps<typeof Link>, 'href' | 'onNavigate'> & { href: string; restoreCatalog?: boolean }) {
  const { navigate } = useWorkspaceNavigation();
  return (
    <Link
      {...props}
      href={href}
      replace={replace}
      scroll={scroll}
      prefetch={false}
      onNavigate={event => {
        event.preventDefault();
        navigate(href, { replace, scroll, restoreCatalog });
      }}
    />
  );
}
