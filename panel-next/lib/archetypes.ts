// Archetype names and classes for the meta view: a Russian name from the vendored dictionary (the HearthPulse
// archetype table, see scripts/sync-models.mjs) and a class slug for the icon. Pure functions, no React.
import labelsFile from './vendor/archetype-labels-ru.json' with { type: 'json' };

export const classSlugs = [
  'deathknight',
  'demonhunter',
  'druid',
  'hunter',
  'mage',
  'paladin',
  'priest',
  'rogue',
  'shaman',
  'warlock',
  'warrior',
] as const;
export type ClassSlug = (typeof classSlugs)[number];

const aliases: Record<string, ClassSlug> = {
  dk: 'deathknight',
  dh: 'demonhunter',
  deathknight: 'deathknight',
  demonhunter: 'demonhunter',
  рыцарьсмерти: 'deathknight',
  охотникнадемонов: 'demonhunter',
  друид: 'druid',
  охотник: 'hunter',
  маг: 'mage',
  паладин: 'paladin',
  жрец: 'priest',
  разбойник: 'rogue',
  шаман: 'shaman',
  чернокнижник: 'warlock',
  воин: 'warrior',
};
for (const slug of classSlugs) aliases[slug] = slug;

/** "Death Knight", "DEATHKNIGHT", "deathknight", "death-knight", "DK" → `deathknight`; anything else → null. */
export function normalizeClass(value: unknown): ClassSlug | null {
  if (typeof value !== 'string') return null;
  const key = value.toLowerCase().replace(/[^a-zа-яё]/g, '');
  return aliases[key] ?? null;
}

// The class is the last word of most archetype names ("Quest Hunter", "Aggro DK"); "-lock" and "-adin" are
// community short forms ("Zoolock", "Turnadin").
const tail =
  /(?:^|[\s-])(death\s?knight|demon\s?hunter|dk|dh|druid|hunter|mage|paladin|priest|rogue|shaman|warlock|warrior)$/i;

/** The class named by the end of an archetype name, or null. */
export function classFromName(name: unknown): ClassSlug | null {
  if (typeof name !== 'string') return null;
  const text = name.trim();
  const match = tail.exec(text);
  if (match) return normalizeClass(match[1]);
  if (/^\S+lock$/i.test(text)) return 'warlock';
  if (/\S+adin$/i.test(text)) return 'paladin';
  return null;
}

/** `class_name` from the API (#104) first; the name's ending is the fallback. */
export function resolveClass(className: unknown, name: unknown): ClassSlug | null {
  return normalizeClass(className) ?? classFromName(name);
}

const labels: Record<string, string> = labelsFile.labels;

/**
 * Russian name from the dictionary, or null: a name missing there stays English (#107). The dictionary writes
 * "dk"/"dh" where HSGuru writes the class out.
 */
export function russianName(name: unknown): string | null {
  if (typeof name !== 'string') return null;
  const key = name.trim().toLowerCase().replace(/\s+/g, ' ');
  if (!key) return null;
  const short = key.replace(/\bdeath ?knight$/, 'dk').replace(/\bdemon ?hunter$/, 'dh');
  return labels[key] ?? labels[short] ?? null;
}

export interface ArchetypeIdentity {
  /** Shown first: the Russian name when the dictionary has one, otherwise the English one. */
  title: string;
  /** The English name, set only when `title` is Russian (the second line). */
  subtitle: string | null;
  classSlug: ClassSlug | null;
}

export function archetypeIdentity(name: unknown, className?: unknown): ArchetypeIdentity {
  const en = typeof name === 'string' ? name.trim() : '';
  const ru = russianName(en);
  return {
    title: ru ?? (en || '—'),
    subtitle: ru && ru.toLowerCase() !== en.toLowerCase() ? en : null,
    classSlug: resolveClass(className, en),
  };
}

/** The icon file for a class (`all` is the neutral one, which has only the 96 px size). */
export function classIconSrc(slug: ClassSlug | null, size: 64 | 96 = 96, base = ''): string {
  return slug ? `${base}/class-icons/${slug}-${size}.webp` : `${base}/class-icons/all-96.webp`;
}
