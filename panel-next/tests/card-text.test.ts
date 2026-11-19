import test from 'node:test';
import assert from 'node:assert/strict';
import fc from 'fast-check';
import { parseCardText, plainCardText } from '../lib/card-text.ts';

const text = (value: string, bold = false, italic = false) => ({ kind: 'text', text: value, bold, italic });

test('battlecry keyword is bold and the rest is plain', () => {
  assert.deepEqual(parseCardText('<b>Боевой клич:</b> нанесите 3 урона.'), [
    text('Боевой клич:', true),
    text(' нанесите 3 урона.'),
  ]);
});

test('deathrattle with italic reminder keeps both styles', () => {
  assert.deepEqual(parseCardText('<b>Предсмертный хрип:</b> <i>призовите</i> копию.'), [
    text('Предсмертный хрип:', true),
    text(' '),
    text('призовите', false, true),
    text(' копию.'),
  ]);
});

test('damage and healing markers become plain numbers', () => {
  assert.equal(plainCardText('Наносит $5 урона. Лечит #3.'), 'Наносит 5 урона. Лечит 3.');
  assert.equal(plainCardText('Deal $5 damage.'), 'Deal 5 damage.');
});

test('[x] is removed and newlines become line breaks', () => {
  assert.deepEqual(parseCardText('[x]<b>Рык</b>\nвторая строка'), [
    text('Рык', true),
    { kind: 'break' },
    text('вторая строка'),
  ]);
});

test('underscore is a non-breaking space; placeholders become X', () => {
  assert.equal(plainCardText('+1_Атака'), '+1 Атака');
  assert.equal(plainCardText('Получите @ брони, {0} раз.'), 'Получите X брони, X раз.');
});

test('unknown tags are dropped, their text stays; broken markup never leaks', () => {
  assert.equal(plainCardText('<u>текст</u> <span class="x">ещё</span>'), 'текст ещё');
  assert.equal(plainCardText('<<b>b>'), 'b');
  assert.equal(plainCardText('</b>до<b>после'), 'допосле');
  assert.deepEqual(parseCardText('<b>не закрыт'), [text('не закрыт', true)]);
});

test('empty and missing input give no tokens', () => {
  assert.deepEqual(parseCardText(''), []);
  assert.deepEqual(parseCardText(null), []);
  assert.deepEqual(parseCardText(undefined), []);
});

const plainChars = fc.string({ unit: 'binary' }).map(value => value.replace(/[<>[\]{}_@$#\r\n]/g, ''));

test('text without markup is shown unchanged', () => {
  fc.assert(
    fc.property(plainChars, value => {
      assert.equal(plainCardText(value), value);
    }),
  );
});

test('no input can leak tags or service symbols into the output', () => {
  const noisy = fc.array(
    fc.oneof(
      fc.constantFrom(
        '<b>',
        '</b>',
        '<i>',
        '</i>',
        '<x>',
        '<',
        '>',
        '[x]',
        '$',
        '#',
        '_',
        '@',
        '{0}',
        '\n',
        '5',
      ),
      fc.string(),
    ),
    { maxLength: 12 },
  );
  fc.assert(
    fc.property(noisy, parts => {
      const output = plainCardText(parts.join(''));
      assert.ok(!/[<>]/.test(output), output);
      assert.ok(!/\[x\]/i.test(output), output);
      assert.ok(!/[$#]\d/.test(output), output);
      assert.ok(!/\{\d+\}/.test(output), output);
    }),
  );
});
