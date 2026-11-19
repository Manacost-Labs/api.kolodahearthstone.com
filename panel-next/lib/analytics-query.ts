// Pure rules for turning page parameters into a request to the analytics backend (no server-only imports).
type ModuleDefinition = {
  params: Record<string, { type?: string; required?: boolean; max?: number }>;
};

/** Rows per page of a module that supports `offset` (the table's own page, so it never shows a second pager); the page number lives in the URL as `page`. */
export const statsPageSize = 25;
/**
 * Modules paged on the server (`page` in the URL, `limit=25&offset`). Both must hold: PHP really applies
 * `offset` (checked against panel/tests/analytics_cli.php: offset 0 and 25 give different rows) and the
 * real total can exceed the `limit` maximum of the registry (decks 2 777 > 100, patches 322 > 100).
 * The others either ignore `offset` in PHP (`bg_minions`, `bg_heroes` solo slice the first `limit` rows) or
 * have fewer rows than their `limit` maximum (archetypes 47/200, hsguru_archetypes 242/500, arena 11/100):
 * they ask for the largest `limit`, never for `offset`, and the table pages on the client.
 */
export const serverPagedModules: readonly string[] = ['decks', 'patches'];

/** What the backend allows for `offset` when the registry does not say (panel/lib/analytics.php: max 10000). */
const defaultOffsetMax = 10000;

/** The last page the backend can serve: its `offset` limit divided by the page size. */
export function maxPage(definition?: ModuleDefinition): number {
  const limit = definition?.params.offset?.max ?? defaultOffsetMax;
  return Math.floor(limit / statsPageSize) + 1;
}

/** The page number of a statistics URL: an integer from 1 to `max`; anything else reads as 1, a far page as the last. */
export function pageNumber(value: string | null, max: number = maxPage()): number {
  const page = Math.floor(Number(value));
  if (!Number.isFinite(page) || page < 1) return 1;
  return Math.min(page, max);
}

const clientOnly = ['action', 'stats', 'rows_q', 'rows', 'page'];
// Modules whose search box has its own name; the inspector button and old links still pass `q`.
const searchAliases: Record<string, string> = { card: 'card_name' };

export function analyticsRequest(query: URLSearchParams, moduleKey: string, definition?: ModuleDefinition) {
  // What the filter form shows: the card name is already in its own field.
  const page = new URLSearchParams(query);
  const backend = new URLSearchParams(query);
  for (const key of clientOnly) backend.delete(key);
  // `stats_q` is the search box of the statistics page, the backend calls it `q`.
  const statsSearch = backend.get('stats_q');
  if (statsSearch !== null) {
    backend.set('q', statsSearch);
    backend.delete('stats_q');
  }
  const alias = searchAliases[moduleKey];
  if (alias) {
    const typed = (backend.get(alias) || '').trim();
    const search = backend.get('q');
    if (!typed && search !== null) {
      backend.set(alias, search);
      page.set(alias, search);
      page.delete('q');
      page.delete('stats_q');
    }
    backend.delete('q');
  }
  // Pages: the panel speaks `page`, the backend `limit`/`offset`; a typed limit or offset never reaches it.
  backend.delete('offset');
  if (serverPagedModules.includes(moduleKey) && definition?.params.offset) {
    backend.set('limit', String(statsPageSize));
    const current = pageNumber(query.get('page'), maxPage(definition));
    backend.set('offset', String((current - 1) * statsPageSize));
    // The address the page keeps (and the pager reads) names the page that was really asked for.
    if (query.has('page') && String(current) !== query.get('page')) page.set('page', String(current));
  } else if (definition?.params.offset && definition.params.limit?.max) {
    // Everything in one answer; the table pages it on the client.
    backend.set('limit', String(definition.params.limit.max));
  }
  backend.set('module', moduleKey);
  const missing = Object.entries(definition?.params || {})
    .filter(([name, rule]) => rule.required && !(backend.get(name) || '').trim())
    .map(([name]) => name);
  return { backend, page, missing };
}

// The adapter answers `{ ok: false, message }`; anything else gets a neutral text, never a raw body.
export function responseMessage(body: unknown, status: number): string {
  const message = typeof body === 'object' && body !== null ? (body as { message?: unknown }).message : null;
  if (typeof message === 'string' && message.trim()) return message.trim().slice(0, 300);
  return 'Не удалось загрузить статистику (код ' + status + ').';
}
