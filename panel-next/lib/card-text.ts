// Hearthstone card text (HearthstoneJSON conventions) as render-ready tokens.
// The output is data, never markup: the UI draws it with React elements.
export type CardTextToken =
  { kind: 'text'; text: string; bold: boolean; italic: boolean } | { kind: 'break' };

const NBSP = ' ';
// Placeholder for values the game computes at play time ("{0}").
const DYNAMIC_VALUE = 'X';

type Cell = { char: string; style: number }; // style bit 1 = bold, bit 2 = italic

// Pass 1: tags become styles; text keeps its characters. Stray angle brackets are dropped.
function stripTags(source: string): Cell[] {
  const cells: Cell[] = [];
  let bold = 0;
  let italic = 0;
  let index = 0;
  while (index < source.length) {
    const char = source[index];
    if (char === '<') {
      const end = source.indexOf('>', index + 1);
      const inner = end < 0 ? '' : source.slice(index + 1, end);
      // A tag is `<...>` without a nested `<`; anything else is a stray bracket.
      if (end >= 0 && !inner.includes('<')) {
        const closing = /^\s*\//.test(inner);
        const name = inner
          .replace(/^\s*\//, '')
          .trim()
          .toLowerCase();
        const delta = closing ? -1 : 1;
        if (name === 'b') bold = Math.max(0, bold + delta);
        else if (name === 'i') italic = Math.max(0, italic + delta);
        index = end + 1;
      } else index += 1;
    } else {
      if (char !== '>' && char !== '\r')
        cells.push({ char, style: (bold > 0 ? 1 : 0) + (italic > 0 ? 2 : 0) });
      index += 1;
    }
  }
  return cells;
}

// `|4(one,two[,five])` picks a word form by the number written right before it.
function pluralForm(forms: string[], before: string) {
  const number = /(\d+)[\s\u00a0]*$/.exec(before);
  if (!number || forms.length < 2) return forms[forms.length - 1];
  const n = Number(number[1]);
  if (forms.length === 2) return n === 1 ? forms[0] : forms[1];
  const last = n % 10;
  const lastTwo = n % 100;
  if (last === 1 && lastTwo !== 11) return forms[0];
  if (last >= 2 && last <= 4 && (lastTwo < 12 || lastTwo > 14)) return forms[1];
  return forms[2] ?? forms[forms.length - 1];
}

// `@` separates variants of the text (base text, then dynamic hints): only the first non-empty
// variant is the card text.
function firstVariant(cells: Cell[]): Cell[] {
  const variants: Cell[][] = [[]];
  for (const cell of cells) {
    if (cell.char === '@') variants.push([]);
    else variants[variants.length - 1].push(cell);
  }
  return variants.find(variant => variant.some(cell => cell.char.trim() !== '')) ?? [];
}

// Pass 2: service markers. Removing one can complete another ("[x<b>]"), so repeat until stable.
function stripMarkers(cells: Cell[]): Cell[] {
  let current = cells;
  for (;;) {
    const joined = current.map(cell => cell.char).join('');
    const next: Cell[] = [];
    let index = 0;
    while (index < current.length) {
      const rest = joined.slice(index);
      const { char, style } = current[index];
      const plural = /^\|4\s*\(([^()]*)\)/.exec(rest);
      if (/^\[x\]/i.test(rest)) index += 3;
      else if (/^[$#]+(?=[\d{])/.test(rest)) index += (rest.match(/^[$#]+/) as RegExpMatchArray)[0].length;
      else if (plural) {
        const form = pluralForm(
          plural[1].split(',').map(item => item.trim()),
          next.map(cell => cell.char).join(''),
        );
        for (const formChar of form) next.push({ char: formChar, style });
        index += plural[0].length;
      } else if (/^\|4\s*\(?/.test(rest)) index += (rest.match(/^\|4\s*\(?/) as RegExpMatchArray)[0].length;
      else if (/^\{\d+\}/.test(rest)) {
        next.push({ char: DYNAMIC_VALUE, style });
        index += (rest.match(/^\{\d+\}/) as RegExpMatchArray)[0].length;
      } else {
        next.push({ char: char === '_' ? NBSP : char, style });
        index += 1;
      }
    }
    if (next.length === current.length && next.every((cell, i) => cell.char === current[i].char)) return next;
    current = next;
  }
}

export function parseCardText(input: unknown): CardTextToken[] {
  const source = input === null || input === undefined ? '' : String(input);
  const tokens: CardTextToken[] = [];
  for (const { char, style } of stripMarkers(firstVariant(stripTags(source)))) {
    if (char === '\n') {
      tokens.push({ kind: 'break' });
      continue;
    }
    const bold = (style & 1) === 1;
    const italic = (style & 2) === 2;
    const last = tokens[tokens.length - 1];
    if (last?.kind === 'text' && last.bold === bold && last.italic === italic) last.text += char;
    else tokens.push({ kind: 'text', text: char, bold, italic });
  }
  return tokens;
}

/** Text without formatting, for accessible names and tests. */
export function plainCardText(input: unknown): string {
  return parseCardText(input)
    .map(token => (token.kind === 'break' ? '\n' : token.text))
    .join('');
}
