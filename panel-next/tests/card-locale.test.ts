import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import fc from 'fast-check';
import {
  LocaleLoader,
  fallbackNote,
  missingNote,
  parseLang,
  parseLocalization,
  requestUrl,
  resolveLang,
  retryAfterMs,
  rowLookup,
  shownLang,
  withLangParam,
  withRender,
  type RowLocale,
} from '../lib/card-locale.ts';

const fixture = (name: string) =>
  JSON.parse(readFileSync(new URL(`./fixtures/card-locale/${name}.json`, import.meta.url), 'utf8'));
const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
const localized = (extra: Record<string, unknown> = {}) => ({
  data: {
    localized: { locale: 'plPL', fallback: false, name: 'M.A.T.K.A.', text: 't', flavor: null, ...extra },
  },
});

test('lang: only ru|en|pl|es are accepted; the URL wins over the saved choice', () => {
  assert.equal(parseLang('pl'), 'pl');
  for (const bad of ['PL', 'plPL', '', null, undefined, 5, 'fr']) assert.equal(parseLang(bad), null);
  assert.equal(resolveLang('es', 'pl'), 'es');
  assert.equal(resolveLang(null, 'pl'), 'pl');
  assert.equal(resolveLang('zz', 'en'), 'en');
  assert.equal(resolveLang(null, 'zz'), 'ru');
  assert.equal(resolveLang(null, null), 'ru');
});

test('withLangParam writes lang for pl and removes it for ru, keeping the rest', () => {
  assert.equal(
    withLangParam('/?card_type=constructed&page=2', 'pl'),
    '/?card_type=constructed&page=2&lang=pl',
  );
  assert.equal(withLangParam('/?lang=pl&page=2', 'ru'), '/?page=2');
  assert.equal(withLangParam('/?lang=pl', 'es'), '/?lang=es');
  assert.equal(withLangParam('/', 'ru'), '/');
});

test('row lookup: card_id, hero_card_id, only dbf, nothing', () => {
  assert.deepEqual(rowLookup({ card_id: 'BE_036', dbf: 128132 }), { id: 'BE_036', dbf: '128132' });
  assert.deepEqual(rowLookup({ card_id: '', hero_card_id: 'TB_BaconShop_HERO_01', dbf: null }), {
    id: 'TB_BaconShop_HERO_01',
    dbf: null,
  });
  assert.deepEqual(rowLookup({ card_id: null, dbf: '77' }), { id: null, dbf: '77' });
  assert.equal(rowLookup({ card_id: null, dbf: null }), null);
  assert.equal(rowLookup({ dbf: 'abc' }), null);
  assert.equal(rowLookup({}), null);
});

test('request url escapes the id and picks the endpoint', () => {
  assert.equal(requestUrl({ id: 'BE_036', dbf: null }, 'plPL'), '/api/v1/card-catalog/BE_036?locale=plPL');
  assert.equal(
    requestUrl({ id: 'a/b?c#d e', dbf: null }, 'esES'),
    '/api/v1/card-catalog/a%2Fb%3Fc%23d%20e?locale=esES',
  );
  assert.equal(
    requestUrl({ id: null, dbf: '128132' }, 'enUS'),
    '/api/v1/card-catalog/dbf/128132?locale=enUS',
  );
  assert.equal(requestUrl({ id: 'X', dbf: '5' }, 'enUS', true), '/api/v1/card-catalog/dbf/5?locale=enUS');
});

test('real BE_036 answers parse in all four languages', () => {
  const names = { ruRU: 'МАТРИАРХ', enUS: 'M.O.T.H.E.R.', plPL: 'M.A.T.K.A.', esES: 'MADRE' };
  for (const [locale, name] of Object.entries(names)) {
    const value = parseLocalization(fixture(`be036-${locale}`));
    assert.equal(value?.name, name);
    assert.equal(value?.locale, locale);
    assert.equal(value?.fallback, false);
    assert.equal(value?.renderUrl, '');
  }
  assert.match(parseLocalization(fixture('be036-plPL'))?.text ?? '', /^<b>Okrzyk bojowy:<\/b>/);
  assert.match(parseLocalization(fixture('be036-esES'))?.text ?? '', /^<b>Grito de batalla:<\/b>/);
});

test('answer validation: fallback, render_url and malformed bodies', () => {
  assert.equal(parseLocalization(localized({ locale: 'enUS', fallback: true }))?.fallback, true);
  assert.equal(parseLocalization(localized())?.renderUrl, '');
  assert.equal(parseLocalization(localized({ render_url: null }))?.renderUrl, '');
  assert.equal(
    parseLocalization(
      localized({
        render_url: 'https://api.kolodahearthstone.com/uploads/card-renders/plPL/512x/BE_036.webp?v=1',
      }),
    )?.renderUrl,
    'https://api.kolodahearthstone.com/uploads/card-renders/plPL/512x/BE_036.webp?v=1',
  );
  assert.equal(parseLocalization(localized({ render_url: 'javascript:alert(1)' }))?.renderUrl, '');
  assert.equal(parseLocalization(localized({ render_url: 5 })), null);
  assert.equal(parseLocalization(localized({ text: null, flavor: null }))?.text, null);
  for (const bad of [
    null,
    'x',
    {},
    { data: null },
    { data: {} },
    { data: { localized: [] } },
    localized({ locale: 'frFR' }),
    localized({ locale: undefined }),
    localized({ name: 5 }),
    localized({ name: '  ' }),
    localized({ text: 5 }),
    localized({ flavor: {} }),
    localized({ fallback: 'no' }),
    localized({ fallback: undefined }),
  ])
    assert.equal(parseLocalization(bad), null, JSON.stringify(bad));
});

test('notes and the render list', () => {
  assert.equal(fallbackNote('pl'), 'Нет перевода на польский — показан английский');
  assert.equal(fallbackNote('es'), 'Нет перевода на испанский — показан английский');
  assert.equal(missingNote, 'Нет в каталоге карт — показаны данные панели');
  const ok = (fallback: boolean, renderUrl: string): RowLocale => ({
    state: 'ok',
    value: { locale: 'plPL', fallback, name: 'n', text: null, flavor: null, renderUrl },
  });
  assert.equal(shownLang('pl', ok(false, '')), 'pl');
  assert.equal(shownLang('pl', ok(true, '')), 'en');
  assert.equal(shownLang('pl', { state: 'loading' }), undefined);
  assert.deepEqual(withRender(['a', 'b'], ok(false, '/r.webp')), ['/r.webp', 'a', 'b']);
  assert.deepEqual(withRender(['a', '/r.webp'], ok(false, '/r.webp')), ['/r.webp', 'a']);
  assert.deepEqual(withRender(['a', 'b'], ok(false, '')), ['a', 'b']);
  assert.deepEqual(withRender(['a', 'b'], { state: 'error' }), ['a', 'b']);
  assert.deepEqual(withRender([], { state: 'missing' }), []);
});

type Call = { url: string; init: RequestInit };
function loaderWith(
  handler: (url: string, init: RequestInit) => Response | Promise<Response>,
  calls: Call[] = [],
) {
  const loader = new LocaleLoader({
    fetch: async (url, init) => {
      calls.push({ url, init });
      return handler(url, init);
    },
    rateLimitDelayMs: 1,
  });
  return { loader, calls };
}

test('loader: public request without credentials, parsed answer cached per language', async () => {
  const { loader, calls } = loaderWith(() => json(fixture('be036-plPL')));
  const lookup = { id: 'BE_036', dbf: '128132' };
  const first = await loader.load('pl', lookup);
  assert.equal(first.state, 'ok');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/card-catalog/BE_036?locale=plPL');
  assert.equal(calls[0].init.credentials, 'omit');
  await loader.load('pl', lookup);
  assert.equal(calls.length, 1, 'the second answer comes from the cache');
  assert.equal(loader.peek('pl', lookup)?.state, 'ok');
  await loader.load('es', lookup);
  assert.equal(calls.length, 2, 'another language is another request');
});

test('loader: 404 by card id retries once by dbf; 404 without dbf is missing', async () => {
  const { loader, calls } = loaderWith(url =>
    url.includes('/dbf/') ? json(fixture('be036-plPL')) : json({ error: { code: 'not_found' } }, 404),
  );
  assert.equal((await loader.load('pl', { id: 'OLD', dbf: '1' })).state, 'ok');
  assert.deepEqual(
    calls.map(call => call.url),
    ['/api/v1/card-catalog/OLD?locale=plPL', '/api/v1/card-catalog/dbf/1?locale=plPL'],
  );
  const none = loaderWith(() => json({ error: {} }, 404));
  assert.equal((await none.loader.load('pl', { id: 'X', dbf: null })).state, 'missing');
  assert.equal(none.calls.length, 1);
  assert.equal(none.loader.peek('pl', { id: 'X', dbf: null })?.state, 'missing', 'a 404 is a settled answer');
  const both = loaderWith(() => json({ error: {} }, 404));
  assert.equal((await both.loader.load('pl', { id: 'X', dbf: '9' })).state, 'missing');
  assert.equal(both.calls.length, 2, 'one retry by dbf and no more');
});

test('loader: 500, network error and invalid body are errors and are not cached', async () => {
  let mode: 'server' | 'network' | 'invalid' | 'ok' = 'server';
  const { loader, calls } = loaderWith(() => {
    if (mode === 'network') throw new TypeError('failed');
    if (mode === 'server') return json({}, 500);
    if (mode === 'invalid') return json({ data: { localized: { name: 1 } } });
    return json(fixture('be036-plPL'));
  });
  const lookup = { id: 'BE_036', dbf: null };
  for (mode of ['server', 'network', 'invalid'] as const) {
    assert.equal((await loader.load('pl', lookup)).state, 'error', mode);
    assert.equal(loader.peek('pl', lookup), undefined);
  }
  assert.equal(calls.length, 3);
  mode = 'ok';
  assert.equal((await loader.load('pl', lookup)).state, 'ok', 'retry asks again');
  const broken = loaderWith(() => new Response('<html>', { status: 200 }));
  assert.equal((await broken.loader.load('pl', lookup)).state, 'error');
});

test('loader: 429 is retried once after a pause, then it is an error', async () => {
  let hits = 0;
  const once = loaderWith(() =>
    ++hits === 1 ? json({}, 429, { 'retry-after': '0' }) : json(fixture('be036-plPL')),
  );
  assert.equal((await once.loader.load('pl', { id: 'A', dbf: null })).state, 'ok');
  assert.equal(once.calls.length, 2);
  const always = loaderWith(() => json({}, 429));
  assert.equal((await always.loader.load('pl', { id: 'A', dbf: null })).state, 'error');
  assert.equal(always.calls.length, 2);
});

test('Retry-After: delta-seconds and HTTP-date, capped at 5 s, otherwise the default pause', () => {
  const now = Date.parse('2026-11-19T15:00:00Z');
  assert.equal(retryAfterMs('2', now, 1500), 2000);
  assert.equal(retryAfterMs(' 3 ', now, 1500), 3000);
  assert.equal(retryAfterMs('120', now, 1500), 5000, 'capped');
  assert.equal(retryAfterMs('Thu, 19 Nov 2026 15:00:04 GMT', now, 1500), 4000, 'HTTP-date in the future');
  assert.equal(retryAfterMs('Thu, 19 Nov 2026 15:10:00 GMT', now, 1500), 5000, 'HTTP-date capped');
  assert.equal(retryAfterMs('Thu, 19 Nov 2026 14:59:00 GMT', now, 1500), 1500, 'past date');
  assert.equal(retryAfterMs('0', now, 1500), 1500);
  assert.equal(retryAfterMs('soon', now, 1500), 1500);
  assert.equal(retryAfterMs('', now, 1500), 1500);
  assert.equal(retryAfterMs(null, now, 1500), 1500);
});

test('loader: 429 waits for an HTTP-date Retry-After before the single retry', async () => {
  const waits: number[] = [];
  const realSetTimeout = globalThis.setTimeout;
  // Record every pause; only the capped retry pause is shortened, the request timeout stays real.
  globalThis.setTimeout = ((callback: () => void, ms?: number) => {
    waits.push(ms ?? 0);
    return realSetTimeout(callback, ms === 5000 ? 0 : ms);
  }) as typeof setTimeout;
  try {
    const date = new Date(Date.now() + 60_000).toUTCString();
    let hits = 0;
    const { loader, calls } = loaderWith(() =>
      ++hits === 1 ? json({}, 429, { 'retry-after': date }) : json(fixture('be036-plPL')),
    );
    assert.equal((await loader.load('pl', { id: 'A', dbf: null })).state, 'ok');
    assert.equal(calls.length, 2);
    assert.ok(waits.includes(5000), `server pause honoured and capped, got ${waits.join(',')}`);
  } finally {
    globalThis.setTimeout = realSetTimeout;
  }
});

test('loader: same request in flight is merged; abort is neither cached nor an error', async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => (release = resolve));
  const { loader, calls } = loaderWith(async (_url, init) => {
    await Promise.race([
      gate,
      new Promise((_, reject) =>
        init.signal?.addEventListener('abort', () => reject(new DOMException('x', 'AbortError'))),
      ),
    ]);
    return json(fixture('be036-plPL'));
  });
  const lookup = { id: 'BE_036', dbf: null };
  const a = loader.load('pl', lookup);
  const b = loader.load('pl', lookup);
  release();
  assert.equal((await a).state, 'ok');
  assert.equal((await b).state, 'ok');
  assert.equal(calls.length, 1);

  const slow = loaderWith(
    (_url, init) =>
      new Promise<Response>((_resolve, reject) =>
        init.signal?.addEventListener('abort', () => reject(new DOMException('x', 'AbortError'))),
      ),
  );
  const controller = new AbortController();
  const pending = slow.loader.load('pl', lookup, controller.signal);
  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(slow.loader.peek('pl', lookup), undefined);
  await assert.rejects(slow.loader.load('pl', lookup, controller.signal), { name: 'AbortError' });
  assert.equal(slow.calls.length, 1, 'an already cancelled request never starts');
});

test('loader: one subscriber leaving does not cancel a request another one still waits for', async () => {
  let release: () => void = () => {};
  const gate = new Promise<void>(resolve => (release = resolve));
  const { loader } = loaderWith(async () => {
    await gate;
    return json(fixture('be036-plPL'));
  });
  const lookup = { id: 'BE_036', dbf: null };
  const leaving = new AbortController();
  const gone = loader.load('pl', lookup, leaving.signal);
  const stays = loader.load('pl', lookup);
  leaving.abort();
  await assert.rejects(gone, { name: 'AbortError' });
  release();
  assert.equal((await stays).state, 'ok');
});

test('loader: a cancelled request finishing late does not drop the newer one from the pool', async () => {
  const gates: (() => void)[] = [];
  const { loader, calls } = loaderWith(
    () =>
      new Promise<Response>(resolve => {
        // Neither request listens to abort: the old one finishes after a new one has been created.
        gates.push(() => resolve(json(fixture('be036-plPL'))));
      }),
  );
  const lookup = { id: 'BE_036', dbf: null };
  const leaving = new AbortController();
  const gone = loader.load('pl', lookup, leaving.signal);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.length, 1);
  leaving.abort();
  await assert.rejects(gone, { name: 'AbortError' });
  const second = loader.load('pl', lookup);
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.length, 2, 'the new request starts its own entry');
  gates[0]?.(); // the old, cancelled entry settles now
  await new Promise(resolve => setTimeout(resolve, 0));
  const third = loader.load('pl', lookup);
  assert.equal(calls.length, 2, 'a third simultaneous request joins the second one');
  gates[1]?.();
  assert.equal((await second).state, 'ok');
  assert.equal((await third).state, 'ok');
  assert.equal(calls.length, 2);
});

test('loader: the cache is bounded', async () => {
  const { loader } = loaderWith(() => json(fixture('be036-plPL')));
  const small = new LocaleLoader({ fetch: async () => json(fixture('be036-plPL')), cacheLimit: 3 });
  for (let i = 0; i < 6; i += 1) await small.load('pl', { id: 'C' + i, dbf: null });
  assert.equal(small.peek('pl', { id: 'C0', dbf: null }), undefined);
  assert.equal(small.peek('pl', { id: 'C5', dbf: null })?.state, 'ok');
  void loader;
});

test('loader: never more than 6 requests at once, one request per card (property)', async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.integer({ min: 1, max: 60 }),
      fc.integer({ min: 0, max: 40 }),
      fc.integer({ min: 0, max: 8 }),
      async (count, duplicates, abortAt) => {
        let active = 0;
        let peak = 0;
        const seen = new Map<string, number>();
        const loader = new LocaleLoader({
          fetch: async (url, init) => {
            active += 1;
            peak = Math.max(peak, active);
            seen.set(url, (seen.get(url) ?? 0) + 1);
            try {
              await new Promise<void>((resolve, reject) => {
                const timer = setTimeout(resolve, 0);
                init.signal?.addEventListener('abort', () => {
                  clearTimeout(timer);
                  reject(new DOMException('x', 'AbortError'));
                });
              });
              return json(fixture('be036-plPL'));
            } finally {
              active -= 1;
            }
          },
        });
        const controller = new AbortController();
        const lookups = Array.from({ length: count + duplicates }, (_, i) => ({
          id: 'C' + (i % count),
          dbf: null,
        }));
        const runs = lookups.map(lookup =>
          loader.load('pl', lookup, controller.signal).catch(() => 'aborted'),
        );
        if (abortAt > 0) setTimeout(() => controller.abort(), abortAt);
        await Promise.all(runs);
        assert.ok(peak <= 6, `peak ${peak}`);
        for (const hits of seen.values()) assert.equal(hits, 1);
        if (abortAt === 0) assert.equal(seen.size, count);
        // A request cancelled before it finished is not remembered.
        for (let i = 0; i < count; i += 1) {
          const cached = loader.peek('pl', { id: 'C' + i, dbf: null });
          if (cached) assert.equal(cached.state, 'ok');
        }
      },
    ),
    { numRuns: 40 },
  );
});
