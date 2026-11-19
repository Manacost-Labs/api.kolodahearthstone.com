// Pure rules for turning page parameters into a request to the analytics backend (no server-only imports).
type ModuleDefinition = { params: Record<string, { type?: string; required?: boolean }> };

/** The page number of a statistics URL: a positive integer, 1 for anything else. */
export function pageNumber(value: string | null): number {
  const page = Math.floor(Number(value));
  return Number.isFinite(page) && page >= 1 && page <= 100000 ? page : 1;
}

const clientOnly = ['action', 'stats', 'rows_q', 'rows', 'page'];
/** Rows per page of a module that supports `offset` (the table's own page, so it never shows a second pager); the page number lives in the URL as `page`. */
export const statsPageSize = 25;
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
  if (definition?.params.offset) {
    backend.set('limit', String(statsPageSize));
    backend.set('offset', String((pageNumber(query.get('page')) - 1) * statsPageSize));
  } else {
    backend.delete('offset');
  }
  backend.set('module', moduleKey);
  const missing = Object.entries(definition?.params || {})
    .filter(([name, rule]) => rule.required && !(backend.get(name) || '').trim())
    .map(([name]) => name);
  return { backend, page, missing };
}

export function missingHint(missing: string[]): string {
  return missing.includes('card_name') ? 'Введите название карты.' : 'Заполните обязательные поля фильтра.';
}

// The adapter answers `{ ok: false, message }`; anything else gets a neutral text, never a raw body.
export function responseMessage(body: unknown, status: number): string {
  const message = typeof body === 'object' && body !== null ? (body as { message?: unknown }).message : null;
  if (typeof message === 'string' && message.trim()) return message.trim().slice(0, 300);
  return 'Не удалось загрузить статистику (код ' + status + ').';
}
