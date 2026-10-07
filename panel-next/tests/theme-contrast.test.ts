import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const css = readFileSync(new URL('../app/globals.css', import.meta.url), 'utf8');

function themeTokens(selector: string): Record<string, string> {
  const start = css.indexOf(selector + '{');
  assert.notEqual(start, -1, `theme block ${selector} is missing`);
  const body = css.slice(start + selector.length + 1, css.indexOf('}', start));
  return Object.fromEntries([...body.matchAll(/--([a-z-]+):(#[0-9a-f]{3,8})/gi)].map(match => [match[1], match[2]]));
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

const light = themeTokens('html');
const themes: Record<string, Record<string, string>> = {
  light,
  dark: { ...light, ...themeTokens('html[data-theme=dark]') },
  tavern: { ...light, ...themeTokens('html[data-theme=tavern]') },
  arcane: { ...light, ...themeTokens('html[data-theme=arcane]') },
};

test('filled buttons keep WCAG AA text contrast in every theme', () => {
  for (const [name, tokens] of Object.entries(themes)) {
    for (const [ink, fill] of [['on-accent', 'accent'], ['on-bad', 'bad']]) {
      assert.ok(tokens[ink] && tokens[fill], `${name}: --${ink} and --${fill} must be defined`);
      const ratio = contrast(tokens[ink], tokens[fill]);
      assert.ok(ratio >= 4.5, `${name}: --${ink} on --${fill} is ${ratio.toFixed(2)}:1`);
    }
  }
});

test('filled controls use the theme ink instead of hard-coded white', () => {
  assert.match(css, /\.button\{[^}]*color:var\(--on-accent\)/);
  assert.match(css, /\.button\.danger\{[^}]*color:var\(--on-bad\)/);
  assert.match(css, /\.page-link\[aria-current\]\{[^}]*color:var\(--on-accent\)/);
});
