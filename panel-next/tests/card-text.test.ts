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

test('underscore is a non-breaking space; {0} becomes X', () => {
  assert.equal(plainCardText('+1_Атака'), '+1\u00a0Атака');
  assert.equal(plainCardText('Получите {0} брони.'), 'Получите X брони.');
});

test('@ separates text variants: only the first one is shown (AV_145, AV_257, mercenary levels)', () => {
  assert.equal(plainCardText('<b>Charge</b>@ <i>({0} left!)</i>@ <i>(Ready!)</i>'), 'Charge');
  assert.equal(plainCardText('Заморозка.@ <i>(@)</i>'), 'Заморозка.');
  assert.equal(plainCardText('@Запасной текст@ещё'), 'Запасной текст');
  assert.equal(plainCardText('+1@+2@+3'), '+1');
});

test('plural forms |4 follow the number before them (BE_005 and friends)', () => {
  assert.equal(
    plainCardText('Вы берете 2 карты через 2 |4(ход,хода,ходов).'),
    'Вы берете 2 карты через 2 хода.',
  );
  assert.equal(plainCardText('1 |4(ход,хода,ходов)'), '1 ход');
  assert.equal(plainCardText('11 |4(ход,хода,ходов)'), '11 ходов');
  assert.equal(plainCardText('22 |4(ход,хода,ходов)'), '22 хода');
  assert.equal(plainCardText('5 |4(ход,хода,ходов)'), '5 ходов');
  assert.equal(plainCardText('in 1 |4 (turn, turns)'), 'in 1 turn');
  assert.equal(plainCardText('in 3 |4 (turn, turns)'), 'in 3 turns');
  assert.equal(plainCardText('Через $2 |4(ход,хода,ходов)'), 'Через 2 хода');
  assert.equal(plainCardText('через {0} |4(ход,хода,ходов)'), 'через X ходов');
  assert.equal(plainCardText('|4(ход,хода,ходов)'), 'ходов');
  assert.equal(plainCardText('сломано |4(ход'), 'сломано ход');
});

test('$ and # before a placeholder are dropped (LT23_031P1_01)', () => {
  assert.equal(plainCardText('Нанесите $' + '{0} урона. Лечит #{1}.'), 'Нанесите X урона. Лечит X.');
});

test('upper-case and garbage tags, [x] in the middle of the text (EDR_493, DALA_BOSS_19p, AT_009)', () => {
  assert.deepEqual(parseCardText('<B>Rush</B>. <I>Quiet</I></>'), [
    text('Rush', true),
    text('. '),
    text('Quiet', false, true),
  ]);
  assert.equal(plainCardText('Получает +1 к атаке.[x]\nвторой абзац'), 'Получает +1 к атаке.\nвторой абзац');
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

test('whitespace-only text without variants is kept as is', () => {
  assert.equal(plainCardText('\t'), '\t');
  assert.equal(plainCardText(' '), ' ');
});

const plainChars = fc.string({ unit: 'binary' }).map(value => value.replace(/[<>[\]{}_@$#|\r\n]/g, ''));

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
      assert.ok(!output.includes('|4'), output);
      assert.ok(!output.includes('@'), output);
    }),
  );
});
