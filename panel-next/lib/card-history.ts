import type { Row } from './types.ts';

export type HistoryVersion = {
  version_id: number;
  observed_at: string;
  event: string;
  snapshot: Row;
};
export type CardHistoryData = {
  entity: string;
  card_id: string;
  versions: HistoryVersion[];
  pagination: { page: number; total: number; total_pages: number };
};
const ignored = new Set([
  'id',
  'created_at',
  'updated_at',
  'first_seen_at',
  'last_seen_at',
  'changed_at',
  'fetched_at',
  'wiki_fetched_at',
  'source_payload',
  'source_payload_json',
  'last_checked_at',
  'last_error',
  'error',
  'import_run_id',
]);
// Sort object keys recursively: transport key order is not a card change.
function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value !== null && typeof value === 'object')
    return (
      '{' +
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, entry]) => JSON.stringify(key) + ':' + canonical(entry))
        .join(',') +
      '}'
    );
  return JSON.stringify(value) ?? 'undefined';
}
export function historyChanges(before: Row, after: Row) {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter(key => !ignored.has(key) && canonical(before[key]) !== canonical(after[key]))
    .sort()
    .map(key => ({ key, before: before[key], after: after[key] }));
}
export function historyIdentity(row: Row, apiBase?: string) {
  const endpoint = apiBase?.split('/').pop();
  const library = apiBase?.match(/\/libraries\/([^/]+)$/)?.[1];
  const entity = library
    ? 'library_card'
    : (
        {
          cards: 'card',
          heroes: 'hero',
          'hero-skins': 'hero_skin',
          'constructed-cards': 'constructed_card',
          'timewarped-cards': 'timewarped_card',
          'library-cards': 'library_card',
          pets: 'pet',
          coins: 'coin',
          'bartender-skins': 'bartender_skin',
        } as Record<string, string>
      )[endpoint || ''];
  const id = row.card_id ?? row.hero_card_id ?? row.skin_id ?? row.pet_id;
  if (!entity || id === null || id === undefined || id === '') return null;
  if (entity === 'library_card' && !row.library && !library) return null;
  return { entity, cardId: entity === 'library_card' ? (row.library || library) + ':' + id : String(id) };
}
export function historyDate(value: string) {
  const date = new Date(value.includes('T') ? value : value.replace(' ', 'T') + 'Z');
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ru-RU');
}
