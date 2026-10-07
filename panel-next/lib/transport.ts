// Pure boundary checks, shared by the server transport and its regression tests.
const actions = new Set(['session', 'list', 'edit', 'new', 'api_tokens', 'wiki_terms', 'analytics_registry']);
export function bridgeTarget(view: string, query: URLSearchParams): string {
  if (view === 'analytics') return '/analytics.php?' + query.toString();
  if (view === 'parsers') return '/parser-control.php';
  if (view !== 'panel') throw new Error('Неизвестный раздел панели.');
  const action = query.get('action') || 'list';
  if (!actions.has(action)) throw new Error('Действие не поддерживается.');
  return '/next-data.php?' + query.toString();
}
export function backendOrigin(value: string): string {
  const url = new URL(value);
  if (url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Некорректный адрес серверного адаптера.');
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname);
  if (!(url.protocol === 'https:' || (loopback && url.protocol === 'http:'))) throw new Error('Серверный адаптер требует HTTPS.');
  return url.origin;
}
export function sessionCookie(cookie: string): string {
  const item = cookie.split(';').map(s => s.trim()).find(s => s.startsWith('koloda_admin='));
  if (!item || !/^koloda_admin=[A-Za-z0-9,-]{1,256}$/.test(item)) return '';
  return item;
}
export function sameOrigin(origin: string | null, requestUrl: string): boolean {
  return origin !== null && origin === new URL(requestUrl).origin;
}
export const mutationActions = new Set(['save', 'delete', 'issue_api_token', 'revoke_api_token', 'save_wiki_terms']);
