// Pure rules for turning page parameters into a request to the analytics backend (no server-only imports).
type ModuleDefinition = { params: Record<string, { type?: string; required?: boolean }> };

const clientOnly = ['action', 'stats', 'rows_q', 'rows'];
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
