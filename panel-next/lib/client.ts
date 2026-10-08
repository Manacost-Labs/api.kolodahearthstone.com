import type { Row } from './types';
export const apiPath = (process.env.NEXT_PUBLIC_PANEL_BASE_PATH || '') + '/api/panel';
export class MutationError extends Error {
  constructor(public payload: Row) {
    super(String(payload.error || payload.message || 'Не удалось выполнить действие.'));
  }
}
export async function mutate(form: FormData) {
  const response = await fetch(apiPath, { method: 'POST', body: form });
  const payload = await response.json();
  if (response.status === 401) {
    // GitHub OAuth is served by PHP, outside the Next.js router.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign('/auth/github');
    throw new Error('Требуется вход через GitHub.');
  }
  if (!response.ok || !payload.ok) throw new MutationError(payload);
  return payload;
}
