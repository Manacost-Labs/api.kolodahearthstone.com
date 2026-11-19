import type { Row } from './types.ts';

export type HistoryVersion = {
  version_id: number;
  observed_at: string;
  event: string;
  snapshot: Row;
  media?: Record<
    string,
    {
      status: string;
      reason?: string | null;
      archive_url?: string | null;
      sha256?: string | null;
      evidence?: string | null;
      captured_at: string;
    }
  >;
};
export type CardHistoryData = {
  entity: string;
  card_id: string;
  versions: HistoryVersion[];
  pagination: { page: number; total: number; total_pages: number };
};

export function archivedHistoryImage(version: HistoryVersion) {
  // The source image of the version comes first: its archive is what the card looked like when it
  // was observed. `local_*` is our own mirror; at the moment of a change it still points to the old
  // file, so it is only a fallback.
  const priorities = [
    'image_url',
    'card_image',
    'card_image_url',
    'framed_image',
    'hero_image_url',
    'local_image_url',
  ];
  const media = Object.entries(version.media || {}).sort(([a], [b]) => {
    const rank = (key: string) => {
      const index = priorities.indexOf(key);
      return index < 0 ? priorities.length : index;
    };
    return rank(a) - rank(b);
  });
  for (const [field, image] of media) {
    const match = image.archive_url?.match(
      /^\/uploads\/card-history-media\/([a-f0-9]{64})\.(png|jpg|webp|gif)$/,
    );
    if (image.status !== 'archived' || !match || image.sha256 !== match[1]) continue;
    return { ...image, field, url: 'https://api.kolodahearthstone.com' + image.archive_url };
  }
  return null;
}
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
// Fields that change when our own mirror or the importer moves, not when the card changes.
const serviceFields = new Set(['source_hash']);
export function isServiceField(key: string) {
  return key.startsWith('local_') || serviceFields.has(key);
}
export function historyChanges(before: Row, after: Row, includeService = false) {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter(
      key =>
        !ignored.has(key) &&
        (includeService || !isServiceField(key)) &&
        canonical(before[key]) !== canonical(after[key]),
    )
    .sort()
    .map(key => ({ key, before: before[key], after: after[key] }));
}
const hasDataChange = (older: HistoryVersion, newer: HistoryVersion) =>
  newer.event !== 'changed' || historyChanges(older.snapshot, newer.snapshot).length > 0;
/**
 * Versions with a real change of card data, newest first. A version without a loaded older
 * neighbour cannot be judged and stays. Versions that differ only in service fields are dropped.
 */
export function dataVersions(versions: HistoryVersion[]) {
  return versions.filter((version, index) => {
    const older = versions[index + 1];
    return !older || hasDataChange(older, version);
  });
}
/** The newest real change and the version before it; null when the loaded versions hold none. */
export function latestDataChange(versions: HistoryVersion[]) {
  const visible = dataVersions(versions);
  for (let index = 0; index < visible.length - 1; index++) {
    if (hasDataChange(visible[index + 1], visible[index]))
      return { after: visible[index].version_id, before: visible[index + 1].version_id };
  }
  return null;
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
