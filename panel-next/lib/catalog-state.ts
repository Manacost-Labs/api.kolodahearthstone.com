import { queryHref } from './model.ts';

export type CatalogView = 'grid' | 'list' | 'tiles';
export function catalogView(value: string | null): CatalogView {
  return value === 'tiles' || value === 'list' ? value : 'grid';
}

// Keep the URL authoritative, and discard filters that the destination cannot expose.
export function catalogHref(query: string, changes: Record<string, string | number | null>) {
  const next = new URLSearchParams(queryHref(query, { page: null, ...changes }).slice(2));
  const type = next.get('card_type') || '';
  next.delete('action');
  if (['hero','hero_skin','coin','constructed','anomaly','quest','reward','trinket'].includes(type)) next.delete('tier');
  if (!['','minion','spell'].includes(type)) {
    next.delete('creature_type');
    next.delete('duos');
  }
  if (['hero','hero_skin','pet','coin','timewarped','constructed'].includes(type)) next.delete('pool');
  if (type !== 'constructed') next.delete('constructed_format');
  if (type !== 'hero_skin') next.delete('rarity');
  if (!['hero','hero_skin','pet','constructed'].includes(type)) next.delete('media');
  if (next.get('view') === 'tiles') next.set('per_page', '15');
  return '/' + (next.size ? '?' + next.toString() : '');
}

export function catalogPreferenceCookie(view: CatalogView) {
  return `hs_catalog_view=${view}; Path=/; Max-Age=31536000; SameSite=Lax`;
}
