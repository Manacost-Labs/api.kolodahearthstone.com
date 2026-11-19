'use client';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  LocaleLoader,
  cardLangStorageKey,
  isAbort,
  langParam,
  lookupKey,
  parseLang,
  rowLookup,
  type CardLang,
  type Lookup,
  type RowLocale,
} from '@/lib/card-locale';
import type { Row } from '@/lib/types';

// One loader per tab: its cache and request pool are shared by the catalogue and the inspector.
let loader: LocaleLoader | null = null;
const sharedLoader = () => (loader ??= new LocaleLoader({ fetch: (url, init) => fetch(url, init) }));

function savedLang(): CardLang | null {
  try {
    return parseLang(window.localStorage.getItem(cardLangStorageKey));
  } catch {
    return null; // storage can be blocked or absent: Russian, no errors
  }
}

function saveLang(lang: CardLang) {
  try {
    window.localStorage.setItem(cardLangStorageKey, lang);
  } catch {
    // The choice still lives in the URL for this tab.
  }
}

function writeUrl(lang: CardLang | null) {
  const next = new URLSearchParams(window.location.search);
  if (lang) next.set('lang', lang);
  else next.delete('lang');
  window.history.replaceState(null, '', window.location.pathname + (next.size ? `?${next}` : ''));
}

// An unknown `?lang=` means Russian and is dropped; the saved choice must not bring it back at once.
let skipRestore = false;

/**
 * The language of card data: `?lang=` of the address, else the saved choice, else Russian. The
 * address changes with replaceState (no navigation, no history entry), like `?card=`.
 */
export function useCardLang(): { lang: CardLang; setLang: (lang: CardLang) => void } {
  const raw = useSearchParams().get('lang');
  const fromUrl = parseLang(raw);
  useEffect(() => {
    if (raw !== null && fromUrl === null) {
      skipRestore = true;
      writeUrl(null);
      return;
    }
    if (raw !== null) return;
    if (skipRestore) {
      skipRestore = false;
      return;
    }
    // A link without `lang` (menu, old bookmark) opens on the last chosen language.
    const saved = savedLang();
    if (saved && saved !== 'ru') writeUrl(saved);
  }, [raw, fromUrl]);
  const setLang = useCallback((next: CardLang) => {
    saveLang(next);
    writeUrl(langParam(next));
  }, []);
  return { lang: fromUrl ?? 'ru', setLang };
}

export type RowLocales = {
  of: (row: Row) => RowLocale;
  /** Rows of the current set whose translation could not be loaded. */
  failed: number;
  retry: () => void;
};

/**
 * Localisations of a set of rows in `lang`. Russian is the panel's own data and needs no request.
 * Rows without an id or DBF are "missing"; the unfinished requests are cancelled when the set or the
 * language changes.
 */
export function useRowLocales(rows: Row[], lang: CardLang): RowLocales {
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  const [round, nextRound] = useReducer((n: number) => n + 1, 0);
  const [failedKeys, setFailedKeys] = useState<ReadonlySet<string>>(new Set());
  const lookups = useMemo(() => rows.map(rowLookup), [rows]);
  // A stable signature: the set is the same while its ids are.
  const signature = lookups.map(lookup => (lookup ? lookupKey(lookup) : '-')).join('|');
  const latest = useRef(lookups);
  latest.current = lookups;

  // biome-ignore lint/correctness/useExhaustiveDependencies: `signature` (the set of ids) and `round` (a retry) restart the loads; the rows themselves come from the ref
  useEffect(() => {
    if (lang === 'ru') return;
    const controller = new AbortController();
    const seen = new Set<string>();
    for (const lookup of latest.current) {
      if (!lookup) continue;
      const key = lang + '|' + lookupKey(lookup);
      if (seen.has(key) || sharedLoader().peek(lang, lookup)) continue;
      seen.add(key);
      sharedLoader()
        .load(lang, lookup, controller.signal)
        .then(
          result => {
            if (result.state === 'error') setFailedKeys(current => new Set(current).add(key));
            rerender();
          },
          error => {
            if (!isAbort(error)) rerender();
          },
        );
    }
    return () => controller.abort();
  }, [lang, signature, round]);

  // Not memoised: the cache fills without the arguments changing, and each render reads it afresh.
  const of = (row: Row): RowLocale => {
    if (lang === 'ru') return { state: 'panel' };
    const lookup: Lookup | null = rowLookup(row);
    if (!lookup) return { state: 'missing' };
    const settled = sharedLoader().peek(lang, lookup);
    if (settled) return settled.state === 'ok' ? { state: 'ok', value: settled.value } : { state: 'missing' };
    return failedKeys.has(lang + '|' + lookupKey(lookup)) ? { state: 'error' } : { state: 'loading' };
  };

  const failed = useMemo(() => {
    if (lang === 'ru') return 0;
    const keys = new Set<string>();
    for (const lookup of lookups) {
      if (!lookup) continue;
      const key = lang + '|' + lookupKey(lookup);
      if (failedKeys.has(key)) keys.add(key);
    }
    return keys.size;
  }, [lang, lookups, failedKeys]);

  const retry = useCallback(() => {
    setFailedKeys(current => {
      const next = new Set(current);
      for (const lookup of latest.current) if (lookup) next.delete(lang + '|' + lookupKey(lookup));
      return next;
    });
    nextRound();
  }, [lang]);

  return { of, failed, retry };
}
