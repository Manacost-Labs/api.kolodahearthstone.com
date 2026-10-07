import 'server-only';
import { cache } from 'react';
import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { backendOrigin, bridgeTarget, sessionCookie } from './transport';
import type { PageData, User } from './types';

const origin = () => backendOrigin(process.env.PANEL_BACKEND_ORIGIN || 'https://api.kolodahearthstone.com');
export async function backendFetch(path: string, cookie: string, init: RequestInit = {}) {
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error('Некорректный путь адаптера.');
  const headers = new Headers(init.headers);
  const session = sessionCookie(cookie);
  if (!session) return Response.json({ok:false,message:'Требуется вход через GitHub.'}, {status:401});
  headers.set('Cookie', session);
  headers.set('Accept', 'application/json');
  return fetch(origin() + path, { ...init, headers, cache: 'no-store', redirect: 'manual', signal: AbortSignal.timeout(20_000) });
}
export async function serverData<T>(view: string, query: URLSearchParams): Promise<T> {
  const jar = await cookies();
  const cookie = jar.get('koloda_admin');
  const response = await backendFetch(bridgeTarget(view, query), cookie ? `koloda_admin=${cookie.value}` : '');
  if (response.status === 401 || response.status === 403) {
    const returnTo = (process.env.NEXT_PUBLIC_PANEL_BASE_PATH || '') + '/';
    redirect((process.env.PANEL_PUBLIC_ORIGIN || 'https://api.kolodahearthstone.com') + '/auth/github?return_to=' + encodeURIComponent(returnTo));
  }
  if (!response.ok) throw new Error('Не удалось загрузить данные панели.');
  if (!response.headers.get('content-type')?.includes('application/json')) throw new Error('Серверный адаптер не установлен.');
  return await response.json() as T;
}
export const getSession = cache(async function getSession() {
  return serverData<{ok:boolean;user:User;logoutCsrf:string;parserCsrf:string}>('panel', new URLSearchParams({action:'session'}));
});
export async function getPage(query: URLSearchParams) {
  const data = await serverData<PageData>('panel', query);
  if (data.version !== 1 || !Array.isArray(data.records)) throw new Error('Несовместимая версия данных панели.');
  return data;
}
