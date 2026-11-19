import 'server-only';
import { cache } from 'react';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { backendOrigin, bridgeTarget, sessionCookie } from './transport';
import { responseMessage } from './analytics-query';
import type { PageData, User } from './types';

const origin = () => backendOrigin(process.env.PANEL_BACKEND_ORIGIN || 'https://api.kolodahearthstone.com');
export async function backendFetch(path: string, cookie: string, init: RequestInit = {}) {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Некорректный путь адаптера.');
  const headers = new Headers(init.headers);
  const session = sessionCookie(cookie);
  if (!session) return Response.json({ ok: false, message: 'Требуется вход через GitHub.' }, { status: 401 });
  headers.set('Cookie', session);
  headers.set('Accept', 'application/json');
  return fetch(origin() + path, {
    ...init,
    headers,
    cache: 'no-store',
    redirect: 'manual',
    signal: AbortSignal.timeout(20_000),
  });
}
function toLogin(): never {
  const returnTo = (process.env.NEXT_PUBLIC_PANEL_BASE_PATH || '') + '/';
  redirect(
    (process.env.PANEL_PUBLIC_ORIGIN || 'https://api.kolodahearthstone.com') +
      '/auth/github?return_to=' +
      encodeURIComponent(returnTo),
  );
}
// Status and message without an exception, for blocks that can fail on their own. 401/403 still lead to login.
export async function serverResult<T>(
  view: string,
  query: URLSearchParams,
): Promise<{ ok: boolean; status: number; message: string; data: T | null }> {
  const jar = await cookies();
  const cookie = jar.get('koloda_admin');
  let response: Response;
  try {
    response = await backendFetch(bridgeTarget(view, query), cookie ? `koloda_admin=${cookie.value}` : '');
  } catch {
    return { ok: false, status: 0, message: 'Сервер статистики не ответил. Обновите страницу.', data: null };
  }
  if (response.status === 401 || response.status === 403) toLogin();
  const json = response.headers.get('content-type')?.includes('application/json');
  const body: unknown = json ? await response.json().catch(() => null) : null;
  if (!response.ok)
    return {
      ok: false,
      status: response.status,
      message: responseMessage(body, response.status),
      data: null,
    };
  if (!json || body === null) throw new Error('Серверный адаптер не установлен.');
  return { ok: true, status: response.status, message: '', data: body as T };
}
export async function serverData<T>(view: string, query: URLSearchParams): Promise<T> {
  const result = await serverResult<T>(view, query);
  if (!result.ok) throw new Error('Не удалось загрузить данные панели.');
  return result.data as T;
}
export const getSession = cache(async function getSession() {
  return serverData<{ ok: boolean; user: User; logoutCsrf: string; parserCsrf: string }>(
    'panel',
    new URLSearchParams({ action: 'session' }),
  );
});
export async function getPage(query: URLSearchParams) {
  const data = await serverData<PageData>('panel', query);
  if (data.version !== 1 || !Array.isArray(data.records))
    throw new Error('Несовместимая версия данных панели.');
  return data;
}
