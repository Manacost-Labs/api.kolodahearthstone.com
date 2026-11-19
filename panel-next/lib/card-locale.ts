// Card text language for the catalogue and the inspector. Pure: no React, no globals except the
// injected `fetch`, so it is unit-tested. Russian comes from the panel data; en/pl/es come from the
// public `/api/v1/card-catalog/{id}?locale=…` (docs/full-card-catalog.md, "Card text language").
import { mediaUrl } from './model.ts';
import type { Row } from './types.ts';

export type CardLang = 'ru' | 'en' | 'pl' | 'es';
export type ApiLocale = 'ruRU' | 'enUS' | 'plPL' | 'esES';

export const cardLangs: readonly CardLang[] = ['ru', 'en', 'pl', 'es'];
export const defaultCardLang: CardLang = 'ru';
export const cardLangStorageKey = 'hs_card_lang';

export const apiLocale: Record<CardLang, ApiLocale> = { ru: 'ruRU', en: 'enUS', pl: 'plPL', es: 'esES' };
export const langOfLocale: Record<ApiLocale, CardLang> = { ruRU: 'ru', enUS: 'en', plPL: 'pl', esES: 'es' };

export const langNames: Record<CardLang, { short: string; full: string; accusative: string }> = {
  ru: { short: 'RU', full: 'Русский', accusative: 'русский' },
  en: { short: 'EN', full: 'Английский', accusative: 'английский' },
  pl: { short: 'PL', full: 'Польский', accusative: 'польский' },
  es: { short: 'ES', full: 'Испанский', accusative: 'испанский' },
};

/** `ru|en|pl|es` or null for a missing or unknown value. */
export function parseLang(value: unknown): CardLang | null {
  return typeof value === 'string' && (cardLangs as readonly string[]).includes(value)
    ? (value as CardLang)
    : null;
}

/** URL first, then the saved preference, then Russian. */
export function resolveLang(fromUrl: unknown, saved: unknown): CardLang {
  return parseLang(fromUrl) ?? parseLang(saved) ?? defaultCardLang;
}

/** The value of `?lang=` for a language: Russian is the default and has no parameter. */
export const langParam = (lang: CardLang): CardLang | null => (lang === defaultCardLang ? null : lang);

/** How a panel row is found in the public catalogue. `null`: it has neither a card id nor a DBF. */
export type Lookup = { id: string | null; dbf: string | null };

const present = (value: unknown) => (value === null || value === undefined ? '' : String(value).trim());

export function rowLookup(row: Row): Lookup | null {
  const id = present(row.card_id) || present(row.hero_card_id);
  const dbf = /^\d+$/.test(present(row.dbf)) ? present(row.dbf) : '';
  if (!id && !dbf) return null;
  return { id: id || null, dbf: dbf || null };
}

/** Stable key of a lookup inside one language. */
export const lookupKey = (lookup: Lookup): string => (lookup.id ? lookup.id : 'dbf:' + lookup.dbf);

export function requestUrl(lookup: Lookup, locale: ApiLocale, byDbf = !lookup.id): string {
  const path = byDbf
    ? '/api/v1/card-catalog/dbf/' + encodeURIComponent(String(lookup.dbf))
    : '/api/v1/card-catalog/' + encodeURIComponent(String(lookup.id));
  return `${path}?locale=${locale}`;
}

export type Localization = {
  locale: ApiLocale;
  fallback: boolean;
  name: string;
  text: string | null;
  flavor: string | null;
  /** A checked image address, or '' when the API gave none. */
  renderUrl: string;
};

/** Validates one API response; `null` means it is not a usable `localized` block. */
export function parseLocalization(body: unknown): Localization | null {
  if (!body || typeof body !== 'object') return null;
  const data = (body as { data?: unknown }).data;
  if (!data || typeof data !== 'object') return null;
  const localized = (data as { localized?: unknown }).localized;
  if (!localized || typeof localized !== 'object' || Array.isArray(localized)) return null;
  const value = localized as Record<string, unknown>;
  const locale = value.locale;
  if (typeof locale !== 'string' || !Object.hasOwn(langOfLocale, locale)) return null;
  if (typeof value.name !== 'string' || value.name.trim() === '') return null;
  for (const key of ['text', 'flavor'] as const)
    if (value[key] !== null && value[key] !== undefined && typeof value[key] !== 'string') return null;
  if (typeof value.fallback !== 'boolean') return null;
  const render = value.render_url;
  if (render !== null && render !== undefined && typeof render !== 'string') return null;
  return {
    locale: locale as ApiLocale,
    fallback: value.fallback,
    name: value.name,
    text: typeof value.text === 'string' ? value.text : null,
    flavor: typeof value.flavor === 'string' ? value.flavor : null,
    renderUrl: mediaUrl(render),
  };
}

export type LoadResult =
  | { state: 'ok'; value: Localization }
  | { state: 'missing' } // the card is not in the full catalogue
  | { state: 'error' }; // network, 5xx, timeout or an invalid response

export type LoaderOptions = {
  fetch: (url: string, init: RequestInit) => Promise<Response>;
  concurrency?: number;
  cacheLimit?: number;
  timeoutMs?: number;
  /** Pause before the single retry of a 429, when the response has no usable Retry-After. */
  rateLimitDelayMs?: number;
};

const abortError = () => new DOMException('Aborted', 'AbortError');
export const isAbort = (error: unknown) => error instanceof DOMException && error.name === 'AbortError';

type Entry = { promise: Promise<LoadResult>; controller: AbortController; subscribers: number };

/**
 * Loads localisations with a bounded pool, an in-memory cache and merged in-flight requests.
 * A cancelled request is neither cached nor an error; failures are not cached, so retrying asks again.
 */
export class LocaleLoader {
  private cache = new Map<string, LoadResult>();
  private inflight = new Map<string, Entry>();
  private waiting: { resume: () => void; signal: AbortSignal }[] = [];
  private running = 0;
  private readonly concurrency: number;
  private readonly cacheLimit: number;
  private readonly timeoutMs: number;
  private readonly rateLimitDelayMs: number;
  private readonly fetcher: LoaderOptions['fetch'];

  constructor(options: LoaderOptions) {
    this.fetcher = options.fetch;
    this.concurrency = options.concurrency ?? 6;
    this.cacheLimit = options.cacheLimit ?? 3000;
    this.timeoutMs = options.timeoutMs ?? 10_000;
    this.rateLimitDelayMs = options.rateLimitDelayMs ?? 1500;
  }

  private key(lang: CardLang, lookup: Lookup) {
    return `${apiLocale[lang]}|${lookupKey(lookup)}`;
  }

  /** The settled answer (found or missing), or undefined while unknown or failed. */
  peek(lang: CardLang, lookup: Lookup): LoadResult | undefined {
    return this.cache.get(this.key(lang, lookup));
  }

  load(lang: CardLang, lookup: Lookup, signal?: AbortSignal): Promise<LoadResult> {
    const key = this.key(lang, lookup);
    const cached = this.cache.get(key);
    if (cached) return Promise.resolve(cached);
    if (signal?.aborted) return Promise.reject(abortError());
    let entry = this.inflight.get(key);
    if (!entry) {
      const controller = new AbortController();
      const created: Entry = {
        controller,
        subscribers: 0,
        promise: this.run(lang, lookup, controller.signal).then(
          result => {
            this.inflight.delete(key);
            if (!controller.signal.aborted && result.state !== 'error') this.remember(key, result);
            return result;
          },
          error => {
            this.inflight.delete(key);
            throw error;
          },
        ),
      };
      // The entry is visible before `run` starts working: a synchronous duplicate must join it.
      this.inflight.set(key, created);
      entry = created;
    }
    const joined = entry;
    joined.subscribers += 1;
    return new Promise<LoadResult>((resolve, reject) => {
      const onAbort = () => {
        joined.subscribers -= 1;
        if (joined.subscribers === 0) {
          joined.controller.abort();
          if (this.inflight.get(key) === joined) this.inflight.delete(key);
        }
        reject(abortError());
      };
      signal?.addEventListener('abort', onAbort, { once: true });
      joined.promise.then(
        result => {
          signal?.removeEventListener('abort', onAbort);
          resolve(result);
        },
        error => {
          signal?.removeEventListener('abort', onAbort);
          reject(error);
        },
      );
    });
  }

  private remember(key: string, result: LoadResult) {
    this.cache.delete(key);
    this.cache.set(key, result);
    while (this.cache.size > this.cacheLimit) {
      const oldest = this.cache.keys().next().value;
      if (oldest === undefined) break;
      this.cache.delete(oldest);
    }
  }

  private acquire(signal: AbortSignal): Promise<void> {
    if (signal.aborted) return Promise.reject(abortError());
    if (this.running < this.concurrency) {
      this.running += 1;
      return Promise.resolve();
    }
    return new Promise((resolve, reject) => {
      const waiter = {
        signal,
        resume: () => {
          signal.removeEventListener('abort', onAbort);
          resolve();
        },
      };
      const onAbort = () => {
        this.waiting = this.waiting.filter(item => item !== waiter);
        reject(abortError());
      };
      signal.addEventListener('abort', onAbort, { once: true });
      this.waiting.push(waiter);
    });
  }

  private release() {
    const next = this.waiting.shift();
    if (next) next.resume(); // the slot passes straight to the next request
    else this.running -= 1;
  }

  private async run(lang: CardLang, lookup: Lookup, signal: AbortSignal): Promise<LoadResult> {
    await this.acquire(signal);
    try {
      const locale = apiLocale[lang];
      const first = await this.fetchOne(requestUrl(lookup, locale), signal);
      // A card id the catalogue does not know may still be found by its DBF: one retry.
      if (first.state === 'missing' && lookup.id && lookup.dbf)
        return await this.fetchOne(requestUrl(lookup, locale, true), signal);
      return first;
    } finally {
      this.release();
    }
  }

  private async fetchOne(url: string, signal: AbortSignal, attempt = 0): Promise<LoadResult> {
    const limited = AbortSignal.any([signal, AbortSignal.timeout(this.timeoutMs)]);
    let response: Response;
    try {
      response = await this.fetcher(url, {
        method: 'GET',
        credentials: 'omit',
        headers: { Accept: 'application/json' },
        signal: limited,
      });
    } catch {
      if (signal.aborted) throw abortError();
      return { state: 'error' };
    }
    if (response.status === 404) return { state: 'missing' };
    if (response.status === 429 && attempt === 0) {
      const wait = Number(response.headers?.get?.('Retry-After'));
      const delay = Number.isFinite(wait) && wait > 0 ? Math.min(wait * 1000, 5000) : this.rateLimitDelayMs;
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          signal.removeEventListener('abort', onAbort);
          resolve();
        }, delay);
        const onAbort = () => {
          clearTimeout(timer);
          reject(abortError());
        };
        signal.addEventListener('abort', onAbort, { once: true });
      });
      return this.fetchOne(url, signal, 1);
    }
    if (!response.ok) return { state: 'error' };
    try {
      const value = parseLocalization(await response.json());
      return value ? { state: 'ok', value } : { state: 'error' };
    } catch {
      if (signal.aborted) throw abortError();
      return { state: 'error' };
    }
  }
}

/** What a row shows for the chosen language. */
export type RowLocale =
  | { state: 'panel' } // Russian, or nothing to load
  | { state: 'loading' }
  | { state: 'missing' }
  | { state: 'error' }
  | { state: 'ok'; value: Localization };

export const fallbackNote = (lang: CardLang) =>
  `Нет перевода на ${langNames[lang].accusative} — показан английский`;
export const missingNote = 'Нет в каталоге карт — показаны данные панели';

/** Language of the text actually shown (a fallback is English whatever was asked for). */
export function shownLang(lang: CardLang, entry: RowLocale): CardLang | undefined {
  if (entry.state !== 'ok') return undefined;
  return entry.value.fallback ? 'en' : lang;
}

/** Inspector images for the normal version: the localised render first, the panel images after it. */
export function withRender(images: string[], entry: RowLocale): string[] {
  if (entry.state !== 'ok' || !entry.value.renderUrl) return images;
  return [entry.value.renderUrl, ...images.filter(url => url !== entry.value.renderUrl)];
}
