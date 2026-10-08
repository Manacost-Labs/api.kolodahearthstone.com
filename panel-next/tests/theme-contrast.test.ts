import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');

// Minimal rule reader: enough for flat theme blocks and control rules, independent of formatting.
const rules = [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(match => ({
  selectors: match[1].split(',').map(selector => selector.replace(/\s+/g, '').replace(/['"]/g, '')),
  declarations: Object.fromEntries(
    match[2]
      .split(';')
      .map(part => part.split(':').map(piece => piece.trim()))
      .filter(([name, value]) => name && value)
      .map(([name, ...value]) => [name, value.join(':')]),
  ) as Record<string, string>,
}));

function declarations(selector: string): Record<string, string> {
  const merged = Object.assign(
    {},
    ...rules.filter(rule => rule.selectors.includes(selector)).map(rule => rule.declarations),
  );
  assert.ok(Object.keys(merged).length, `rule ${selector} is missing`);
  return merged;
}

function luminance(hex: string): number {
  const value = hex.replace('#', '');
  const full = value.length === 3 ? [...value].map(char => char + char).join('') : value.slice(0, 6);
  const [r, g, b] = [0, 2, 4].map(offset => {
    const channel = parseInt(full.slice(offset, offset + 2), 16) / 255;
    return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

// Soft fills are translucent (#rrggbbaa); badges sit on the theme surface.
function over(fill: string, backdrop: string): string {
  const value = fill.replace('#', '');
  const alpha = value.length === 8 ? parseInt(value.slice(6), 16) / 255 : 1;
  const channel = (hex: string, offset: number) =>
    parseInt(hex.replace('#', '').slice(offset, offset + 2), 16);
  return (
    '#' +
    [0, 2, 4]
      .map(offset => Math.round(alpha * channel(fill, offset) + (1 - alpha) * channel(backdrop, offset)))
      .map(part => part.toString(16).padStart(2, '0'))
      .join('')
  );
}

const base = declarations('html');
const themes: Record<string, Record<string, string>> = {
  light: base,
  dark: { ...base, ...declarations('html[data-theme=dark]') },
  tavern: { ...base, ...declarations('html[data-theme=tavern]') },
  arcane: { ...base, ...declarations('html[data-theme=arcane]') },
};

test('filled buttons keep WCAG AA text contrast in every theme', () => {
  for (const [name, tokens] of Object.entries(themes)) {
    for (const [ink, fill] of [
      ['on-accent', 'accent'],
      ['on-bad', 'bad'],
    ]) {
      const [inkColor, fillColor] = [tokens['--' + ink], tokens['--' + fill]];
      assert.ok(inkColor && fillColor, `${name}: --${ink} and --${fill} must be defined`);
      const ratio = contrast(inkColor, fillColor);
      assert.ok(ratio >= 4.5, `${name}: --${ink} on --${fill} is ${ratio.toFixed(2)}:1`);
    }
  }
});

test('filled controls use the theme ink instead of hard-coded white', () => {
  assert.equal(declarations('.button').color, 'var(--on-accent)');
  assert.equal(declarations('.button.danger').color, 'var(--on-bad)');
  assert.equal(declarations('.page-link[aria-current]').color, 'var(--on-accent)');
});

test('text tokens keep WCAG AA contrast on their backgrounds in every theme', () => {
  for (const [name, tokens] of Object.entries(themes)) {
    const pairs: [string, string][] = [
      ['--text', tokens['--bg']],
      ['--muted', tokens['--surface']],
      ['--accent', tokens['--bg']],
      ['--accent', tokens['--surface']],
      ['--accent', over(tokens['--accent-soft'], tokens['--surface'])],
    ];
    for (const status of ['good', 'bad', 'warn', 'info']) {
      assert.ok(tokens[`--${status}`] && tokens[`--${status}-soft`], `${name}: --${status} must be defined`);
      pairs.push([`--${status}`, over(tokens[`--${status}-soft`], tokens['--surface'])]);
    }
    for (const [ink, backdrop] of pairs) {
      const ratio = contrast(tokens[ink], backdrop);
      assert.ok(ratio >= 4.5, `${name}: ${ink} on ${backdrop} is ${ratio.toFixed(2)}:1`);
    }
  }
});

test('warning and info badges no longer borrow the accent colour', () => {
  assert.equal(declarations('.badge.warning').color, 'var(--warn)');
  assert.equal(declarations('.badge.info').color, 'var(--info)');
});
