import fs from 'node:fs/promises';
await fs.mkdir(new URL('../lib/vendor/', import.meta.url), { recursive: true });
for (const name of ['parser-control-view', 'parsing-reliability']) {
  const source = await fs.readFile(new URL('../../panel/assets/' + name + '.js', import.meta.url), 'utf8');
  await fs.writeFile(new URL('../lib/vendor/' + name + '.cjs', import.meta.url), source);
}
await fs.mkdir(new URL('../app/generated/', import.meta.url), { recursive: true });
const tiles = await fs.readFile(
  new URL('../../panel/examples/horizontal-art-tiles.css', import.meta.url),
  'utf8',
);
await fs.writeFile(
  new URL('../app/generated/deck-tiles-base.css', import.meta.url),
  tiles.replace(/@font-face\s*\{[^}]*\}/g, ''),
);
