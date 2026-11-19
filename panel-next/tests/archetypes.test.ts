import test from 'node:test';
import assert from 'node:assert/strict';
import {
  archetypeIdentity,
  classFromName,
  normalizeClass,
  resolveClass,
  russianName,
} from '../lib/archetypes.ts';

test('normalizeClass reads every spelling of a class', () => {
  for (const value of ['Death Knight', 'DEATHKNIGHT', 'deathknight', 'DK', 'death-knight', 'Рыцарь смерти'])
    assert.equal(normalizeClass(value), 'deathknight', value);
  for (const value of ['Demon Hunter', 'DEMONHUNTER', 'dh'])
    assert.equal(normalizeClass(value), 'demonhunter');
  assert.equal(normalizeClass('Hunter'), 'hunter');
  assert.equal(normalizeClass('Neutral'), null);
  assert.equal(normalizeClass(null), null);
  assert.equal(normalizeClass(''), null);
});

test('classFromName takes the class from the end of the name', () => {
  assert.equal(classFromName('Quest Death Knight'), 'deathknight');
  assert.equal(classFromName('Aggro DK'), 'deathknight');
  assert.equal(classFromName('Control Priest'), 'priest');
  assert.equal(classFromName('Zoolock'), 'warlock');
  assert.equal(classFromName('Turnadin'), 'paladin');
  assert.equal(classFromName('Hunter Quest Mage'), 'mage');
  assert.equal(classFromName('Splendiferous Whizbang'), null);
  assert.equal(classFromName(undefined), null);
});

test('resolveClass prefers class_name and falls back to the name', () => {
  assert.equal(resolveClass('Druid', 'Zoolock'), 'druid');
  assert.equal(resolveClass(null, 'Zoolock'), 'warlock');
  assert.equal(resolveClass('', 'Splendiferous Whizbang'), null);
});

test('russianName and archetypeIdentity use the dictionary', () => {
  assert.equal(russianName('Quest Mage'), 'Квест Маг');
  assert.equal(russianName('QUEST  mage'), 'Квест Маг');
  assert.equal(russianName('Aggro Death Knight'), 'Агро ДК');
  assert.equal(russianName('Xyzzy Plugh'), null);
  const known = archetypeIdentity('Quest Mage', 'Mage');
  assert.deepEqual(known, { title: 'Квест Маг', subtitle: 'Quest Mage', classSlug: 'mage' });
  const unknown = archetypeIdentity('Xyzzy Plugh', 'Mage');
  assert.deepEqual(unknown, { title: 'Xyzzy Plugh', subtitle: null, classSlug: 'mage' });
  assert.equal(archetypeIdentity('').title, '—');
});

test('a name the dictionary lacks stays English, the class still comes from its ending', () => {
  assert.equal(russianName('Yogg Warrior'), null);
  assert.equal(russianName('Some New Archetype Mage'), null);
  assert.deepEqual(archetypeIdentity('Yogg Warrior'), {
    title: 'Yogg Warrior',
    subtitle: null,
    classSlug: 'warrior',
  });
});

test('every class has both icon files, and nothing the panel serves sits in public/class-icons', async () => {
  const { existsSync } = await import('node:fs');
  const { classSlugs } = await import('../lib/archetypes.ts');
  for (const slug of classSlugs)
    for (const size of [64, 96])
      assert.ok(
        existsSync(new URL(`../assets/class-icons/${slug}-${size}.webp`, import.meta.url)),
        `${slug}-${size}`,
      );
  assert.ok(existsSync(new URL('../assets/class-icons/all-96.webp', import.meta.url)));
  // nginx sends only /, /api/panel and /_next/ to panel-next: a public/ image would 404 on prod.
  assert.ok(!existsSync(new URL('../public/class-icons', import.meta.url)));
});
