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

// Real strings from the public API (2026-11-19).   is a non-breaking space, as in the source.
test('pl: |4(…) after a number and a non-breaking space (EDR_259)', () => {
  assert.equal(
    plainCardText(
      '<b>Okrzyk bojowy:</b> Rzuć zaklęcie o najwyższym koszcie z twojej ręki jako Aurę działającą przez 3 |4(turę,tury,tur).',
      'pl',
    ),
    'Okrzyk bojowy: Rzuć zaklęcie o najwyższym koszcie z twojej ręki jako Aurę działającą przez 3 tury.',
  );
});

test('pl: 1 takes the first form (JAIL_470)', () => {
  assert.equal(
    plainCardText('Zadaj 1 pkt. Wystrzel 1 |4(raz,razy,razy)!', 'pl'),
    'Zadaj 1 pkt. Wystrzel 1 raz!',
  );
});

test('pl and ru count three forms differently', () => {
  const form = (n: number, lang: 'ru' | 'pl') => plainCardText(`${n} |4(a,b,c)`, lang);
  assert.equal(form(1, 'pl'), '1 a');
  assert.equal(form(2, 'pl'), '2 b');
  assert.equal(form(4, 'pl'), '4 b');
  assert.equal(form(5, 'pl'), '5 c');
  assert.equal(form(12, 'pl'), '12 c');
  assert.equal(form(14, 'pl'), '14 c');
  assert.equal(form(22, 'pl'), '22 b');
  assert.equal(form(25, 'pl'), '25 c');
  assert.equal(form(0, 'pl'), '0 c');
  // 21 and 31 are the divergence: "many" in pl, "one" in ru.
  assert.equal(form(21, 'pl'), '21 c');
  assert.equal(form(21, 'ru'), '21 a');
  assert.equal(form(31, 'pl'), '31 c');
  assert.equal(form(11, 'ru'), '11 c');
  assert.equal(form(23, 'ru'), '23 b');
});

test('en and es have two forms: exactly 1, or anything else', () => {
  for (const lang of ['en', 'es'] as const) {
    assert.equal(plainCardText('1 |4(card,cards)', lang), '1 card');
    assert.equal(plainCardText('2 |4(card,cards)', lang), '2 cards');
    assert.equal(plainCardText('21 |4(card,cards)', lang), '21 cards');
    assert.equal(plainCardText('0 |4(card,cards)', lang), '0 cards');
  }
});

test('default language is ru', () => {
  assert.equal(plainCardText('21 |4(a,b,c)'), '21 a');
});

test('an upper-case </I> closes italics (EDR_493)', () => {
  assert.deepEqual(parseCardText('<b>Grito de batalla:</b>\nUno. <i>(Conservan coste)</I>.', 'es'), [
    text('Grito de batalla:', true),
    { kind: 'break' },
    text('Uno. '),
    text('(Conservan coste)', false, true),
    text('.'),
  ]);
  assert.equal(
    plainCardText('<b>Okrzyk bojowy:</b> Przemień.\n<i>(Zachowają statystyki i koszt).</I>', 'pl'),
    'Okrzyk bojowy: Przemień.\n(Zachowają statystyki i koszt).',
  );
});

test('@ keeps the first variant and {0} becomes X (CATA_131)', () => {
  assert.equal(
    plainCardText(
      '<b>Grito de batalla:</b> Obtienes un cristal. <i>(¡{0} más!)</i>.@<b>Grito de batalla:</b> Obtienes un cristal. <i>(¡Listo!)</i>.@<b>Grito de batalla:</b> Obtienes un cristal.',
      'es',
    ),
    'Grito de batalla: Obtienes un cristal. (¡X más!).',
  );
  assert.equal(
    plainCardText(
      '<b>Okrzyk bojowy:</b>\nOtrzymujesz. <i>(Jeszcze {0}!)</i>@<b>Okrzyk bojowy:</b>\nKoniec.',
      'pl',
    ),
    'Okrzyk bojowy:\nOtrzymujesz. (Jeszcze X!)',
  );
});

test('[x] is dropped from es text and line breaks stay (LOE_006)', () => {
  assert.equal(
    plainCardText(
      '[x]<b>Grito de batalla:\nDescubre</b> una carta con\n<b>Último aliento</b>. Cuesta\n(1) cristal menos.',
      'es',
    ),
    'Grito de batalla:\nDescubre una carta con\nÚltimo aliento. Cuesta\n(1) cristal menos.',
  );
});

test('$3 is a plain number in pl and es (LOE_002)', () => {
  assert.equal(
    plainCardText(
      'Zadaj $3 pkt. obrażeń. Wtasuj do twojej talii „Buzującą pochodnię” zadającą 6 pkt. obrażeń.',
      'pl',
    ),
    'Zadaj 3 pkt. obrażeń. Wtasuj do twojej talii „Buzującą pochodnię” zadającą 6 pkt. obrażeń.',
  );
  assert.equal(
    plainCardText('Inflige $3 p. de daño. Mete en tu mazo una antorcha.', 'es'),
    'Inflige 3 p. de daño. Mete en tu mazo una antorcha.',
  );
});
