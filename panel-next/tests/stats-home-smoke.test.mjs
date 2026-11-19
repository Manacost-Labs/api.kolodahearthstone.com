import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
// «Сводка»: `/?action=analytics` without `stats` shows four mode cards from the existing modules.
const origin = process.env.PANEL_TEST_ORIGIN || 'http://127.0.0.1:4180';
const fixture = process.env.PANEL_FIXTURE_ORIGIN || 'http://127.0.0.1:18768';
const cookie = 'koloda_admin=fixture-session';
const registry = JSON.parse(
  readFileSync(new URL('./fixtures/analytics-registry.json', import.meta.url), 'utf8'),
);
const get = async search => {
  const response = await fetch(origin + '/?action=analytics' + search, { headers: { Cookie: cookie } });
  return { status: response.status, html: await response.text() };
};
const log = async () =>
  (await (await fetch(fixture + '/test/requests')).json()).filter(item => item.view === 'analytics');
const failing = modules => fetch(fixture + '/test/fail?modules=' + modules.join(','));
const cardsOf = html =>
  Object.fromEntries(
    [...html.matchAll(/<a\b[^>]*class="stats-home__card"[^>]*>[\s\S]*?<\/a>/g)].map(([block]) => [
      /data-card="(\w+)"/.exec(block)[1],
      {
        block,
        href: /href="([^"]+)"/.exec(block)[1].replaceAll('&amp;', '&'),
        status: /data-status="(\w+)"/.exec(block)[1],
        rows: (block.match(/stats-home__row/g) || []).length,
        text: block.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' '),
      },
    ]),
  );

test('without `stats` the summary opens: four cards with a date, three rows and a link into the mode', async () => {
  const { status, html } = await get('');
  assert.equal(status, 200);
  assert.ok(html.includes('<h1>Статистика · Сводка</h1>'), 'heading');
  const cards = cardsOf(html);
  assert.deepEqual(Object.keys(cards), ['standard', 'wild', 'bg', 'arena']);
  assert.equal(cards.standard.href, '/?action=analytics&stats=meta&format=standard');
  assert.equal(cards.wild.href, '/?action=analytics&stats=meta&format=wild');
  assert.equal(cards.bg.href, '/?action=analytics&stats=bg_heroes');
  assert.equal(cards.arena.href, '/?action=analytics&stats=arena');
  for (const card of Object.values(cards)) {
    assert.equal(card.status, 'ok');
    assert.equal(card.rows, 3);
    assert.match(card.text, /Данные на 19 нояб\. 2026/);
    assert.ok(!/NaN|undefined|\[object/.test(card.text), card.text);
  }
  assert.match(cards.arena.text, /Рыцарь смерти|Маг|Воин|Охотник/);
  assert.match(cards.bg.text, /Герой 1/);
  assert.match(cards.standard.text, /%/);
});

test('`stats=summary` and an empty `stats=` open the summary too; the menu names it first', async () => {
  for (const search of ['&stats=summary', '&stats=']) {
    const { html } = await get(search);
    assert.ok(html.includes('<h1>Статистика · Сводка</h1>'), search);
  }
});

test('old links keep their module: `stats=meta` is «Мета», an unknown key falls back to «Мета», not to the summary', async () => {
  for (const search of ['&stats=meta', '&stats=no_such_module']) {
    const { status, html } = await get(search);
    assert.equal(status, 200);
    assert.ok(html.includes('<h1>' + registry.modules.meta.title + '</h1>'), search);
    assert.ok(!html.includes('stats-home__card'), search);
  }
});

test('the summary asks only the four modules, in the modes it names, once each', async () => {
  const before = (await log()).length;
  await get('');
  const asked = (await log()).slice(before);
  assert.deepEqual(asked.map(item => item.module).sort(), ['arena', 'bg_heroes', 'meta', 'meta']);
  const queries = asked.map(item => item.query);
  assert.ok(queries.some(query => query.includes('format=standard')));
  assert.ok(queries.some(query => query.includes('format=wild')));
  assert.ok(queries.some(query => query.includes('mode=solo')));
});

test('a failing module shows on its own card; the other three keep working', async () => {
  await failing(['bg_heroes']);
  try {
    const { status, html } = await get('');
    assert.equal(status, 200);
    const cards = cardsOf(html);
    assert.equal(cards.bg.status, 'error');
    assert.match(cards.bg.text, /Не удалось загрузить: Не удалось загрузить статистику/);
    assert.equal(cards.bg.href, '/?action=analytics&stats=bg_heroes');
    for (const key of ['standard', 'wild', 'arena']) {
      assert.equal(cards[key].status, 'ok', key);
      assert.equal(cards[key].rows, 3, key);
    }
    // Both meta cards share a module: one failing module fails exactly its cards.
    await failing(['meta']);
    const meta = cardsOf((await get('')).html);
    assert.deepEqual(
      ['standard', 'wild', 'bg', 'arena'].map(key => meta[key].status),
      ['error', 'error', 'ok', 'ok'],
    );
  } finally {
    await failing([]);
  }
});

test('all four modules failing still renders the page with four error cards', async () => {
  await failing(['meta', 'bg_heroes', 'arena']);
  try {
    const { status, html } = await get('');
    assert.equal(status, 200);
    assert.ok(html.includes('<h1>Статистика · Сводка</h1>'));
    assert.deepEqual(
      Object.values(cardsOf(html)).map(card => card.status),
      ['error', 'error', 'error', 'error'],
    );
  } finally {
    await failing([]);
  }
});
