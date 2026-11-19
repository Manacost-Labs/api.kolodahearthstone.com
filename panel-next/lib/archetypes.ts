// Archetype names and classes for the meta view: a Russian name from the vendored dictionary (the HearthPulse
// archetype table, see scripts/sync-models.mjs) and a class slug for the icon. Pure functions, no React.
import labelsFile from './vendor/archetype-labels-ru.json' with { type: 'json' };
import termsFile from './vendor/archetype-terms-ru.json' with { type: 'json' };

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
type Term = { p: string } | { s: string } | { q: string };
const terms = termsFile.terms as Record<string, Term>;

// The Russian label as HearthPulse builds it (server/modules/constructedArchetypes/archetypeLabel.ts and
// autoArchetypeLabel.ts): the exact translation; else the automatic label when it covers every word; else the
// longest contained translation with the uncovered words in brackets; else the automatic label with its unknown
// words; else nothing (the English name stays).

const classWords: [string, string][] = [
  ['demon hunter', 'Охотник на демонов'],
  ['death knight', 'Рыцарь смерти'],
  ['deathknight', 'Рыцарь смерти'],
  ['demonhunter', 'Охотник на демонов'],
  ['dh', 'Охотник на демонов'],
  ['dk', 'Рыцарь смерти'],
  ['priest', 'Жрец'],
  ['warlock', 'Чернокнижник'],
  ['shaman', 'Шаман'],
  ['rogue', 'Разбойник'],
  ['warrior', 'Воин'],
  ['druid', 'Друид'],
  ['hunter', 'Охотник'],
  ['mage', 'Маг'],
  ['paladin', 'Паладин'],
];
const fusedClass: [RegExp, string][] = [
  [/^(.+?)-?lock$/, 'Чернокнижник'],
  [/^(.+?)adin$/, 'Паладин'],
];
const fusedNames: Record<string, string> = { zoolock: 'Зоолок' };

function splitClass(tokens: string[]) {
  for (const [english, russian] of classWords) {
    const size = english.split(' ').length;
    if (tokens.length >= size && tokens.slice(-size).join(' ') === english)
      return { rest: tokens.slice(0, -size), className: russian, fusedStem: false };
  }
  const last = tokens.at(-1) ?? '';
  if (fusedNames[last]) return { rest: tokens.slice(0, -1), className: fusedNames[last], fusedStem: false };
  for (const [pattern, russian] of fusedClass) {
    const match = last.match(pattern);
    if (match)
      return {
        rest: [...tokens.slice(0, -1), match[1].replace(/-$/, '')],
        className: russian,
        fusedStem: true,
      };
  }
  return { rest: tokens, className: null, fusedStem: false };
}

function joinSuffixes(suffixes: string[]): string {
  const on = suffixes.filter(value => value.startsWith('на ')).map(value => value.slice(3));
  const genitive = suffixes.filter(value => !/^(на|с|со|без) /.test(value));
  const withPhrases = suffixes.filter(value => /^(с|со|без) /.test(value));
  const joined = on.length
    ? `на ${on.length > 1 ? `${on.slice(0, -1).join(', ')} и ${on.at(-1)}` : on[0]}`
    : '';
  return [...genitive, joined, ...withPhrases].filter(Boolean).join(' ');
}

/** «<Ключевая карта или механика> <Класс>» from the term list; `unknown` lists the words left in English. */
export function autoArchetypeLabel(name: string): { label: string; unknown: string[] } | null {
  const tokens = name
    .trim()
    .replace(/[’`]/g, "'")
    .replace(/\s+/g, ' ')
    .toLowerCase()
    .split(' ')
    .filter(Boolean);
  if (!tokens.length) return null;
  const { rest, className, fusedStem } = splitClass(tokens);
  if (!className) return null;
  const originals = name.trim().split(/\s+/);
  const prefix: string[] = [];
  const suffix: string[] = [];
  const qualifier: string[] = [];
  const unknown: string[] = [];
  for (let index = 0; index < rest.length;) {
    let size = Math.min(3, rest.length - index);
    for (; size > 0; size -= 1) {
      const term = terms[rest.slice(index, index + size).join(' ')];
      if (!term) continue;
      const [bucket, value] =
        'p' in term ? [prefix, term.p] : 's' in term ? [suffix, term.s] : [qualifier, term.q];
      if (!bucket.includes(value)) bucket.push(value);
      break;
    }
    if (size === 0) {
      const original =
        fusedStem && index === rest.length - 1
          ? (originals[index] ?? rest[index]).slice(0, rest[index].length)
          : (originals[index] ?? rest[index]);
      unknown.push(original);
      prefix.push(original);
      size = 1;
    }
    index += size;
  }
  const demons = className === 'Охотник на демонов' ? suffix.indexOf('на демонах') : -1;
  if (demons >= 0) {
    suffix.splice(demons, 1);
    prefix.unshift('Демонический');
  }
  const head = [...prefix, className, joinSuffixes(suffix)].filter(Boolean).join(' ');
  return { label: qualifier.length ? `${head} (${qualifier.join(', ')})` : head, unknown };
}

/** Russian name of an HSGuru archetype, or null. The dictionary writes "dk"/"dh" where HSGuru writes the class out. */
export function russianName(name: unknown): string | null {
  if (typeof name !== 'string') return null;
  const trimmed = name.trim().replace(/\s+/g, ' ');
  if (!trimmed) return null;
  const short = trimmed.replace(/\bdeath ?knight$/i, 'DK').replace(/\bdemon ?hunter$/i, 'DH');
  const exact = labels[trimmed.toLowerCase()] ?? labels[short.toLowerCase()];
  if (exact) return exact;
  const key = short.toLowerCase();
  let best = '';
  for (const candidate of Object.keys(labels))
    if (candidate.length > best.length && key.includes(candidate)) best = candidate;
  const auto = autoArchetypeLabel(trimmed);
  if (auto && !auto.unknown.length) return auto.label;
  if (best) {
    const start = key.indexOf(best);
    const qualifier = `${short.slice(0, start)} ${short.slice(start + best.length)}`
      .replace(/\s+/g, ' ')
      .replace(/^[\s\-–—:·,]+|[\s\-–—:·,]+$/g, '');
    return `${labels[best]} (${qualifier})`;
  }
  return auto?.label ?? null;
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
