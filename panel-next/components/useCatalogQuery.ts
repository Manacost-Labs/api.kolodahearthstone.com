'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { catalogHref, catalogPreferenceCookie, catalogView, type CatalogView } from '@/lib/catalog-state';
import { useWorkspaceNavigation } from './WorkspaceNavigation';

export function useCatalogQuery(query: string) {
  const { pending, navigate, beforeNavigate } = useWorkspaceNavigation();
  const [draftQuery, setDraftQuery] = useState(query);
  const [search, setSearch] = useState(new URLSearchParams(query).get('q') || '');
  const desired = useRef(query);
  const draftSearch = useRef(search);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelSearch = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => {
    const unsubscribe = beforeNavigate(cancelSearch);
    window.addEventListener('popstate', cancelSearch);
    return () => {
      unsubscribe();
      window.removeEventListener('popstate', cancelSearch);
      cancelSearch();
    };
  }, [beforeNavigate, cancelSearch]);
  useEffect(() => {
    if (pending) return;
    desired.current = query;
    // Adopt the settled URL as the new draft; typing in between keeps its own draft below.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDraftQuery(query);
    // A response arriving while the user is still typing must not replace that draft.
    if (!timer.current) {
      const value = new URLSearchParams(query).get('q') || '';
      draftSearch.current = value;
      setSearch(value);
    }
  }, [query, pending]);
  const go = useCallback(
    (changes: Record<string, string | number | null>, replace = false) => {
      cancelSearch();
      const href = catalogHref(desired.current, { q: draftSearch.current, ...changes });
      desired.current = href.slice(2);
      setDraftQuery(desired.current);
      navigate(href, { replace, scroll: false });
    },
    [cancelSearch, navigate],
  );
  const changeSearch = (value: string) => {
    setSearch(value);
    draftSearch.current = value;
    cancelSearch();
    timer.current = setTimeout(() => go({}, true), 350);
  };
  const mode = (value: CatalogView) => {
    // biome-ignore lint/suspicious/noDocumentCookie: a plain preference cookie; the server reads it on first render
    document.cookie = catalogPreferenceCookie(value);
    go({ view: value }, true);
  };
  const params = new URLSearchParams(draftQuery);
  return { pending, params, search, changeSearch, go, mode, view: catalogView(params.get('view')) };
}
