// Hearthstone card text (HearthstoneJSON conventions) as render-ready tokens.
// The output is data, never markup: the UI draws it with React elements.
export type CardTextToken =
  { kind: 'text'; text: string; bold: boolean; italic: boolean } | { kind: 'break' };

const NBSP = ' ';
// Placeholder for values the game computes at play time ("@", "{0}").
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
      if (/^\[x\]/i.test(rest)) index += 3;
      else if (/^[$#]+\d/.test(rest)) index += (rest.match(/^[$#]+/) as RegExpMatchArray)[0].length;
      else if (/^\{\d+\}/.test(rest)) {
        next.push({ char: DYNAMIC_VALUE, style });
        index += (rest.match(/^\{\d+\}/) as RegExpMatchArray)[0].length;
      } else {
        next.push({ char: char === '@' ? DYNAMIC_VALUE : char === '_' ? NBSP : char, style });
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
  for (const { char, style } of stripMarkers(stripTags(source))) {
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
