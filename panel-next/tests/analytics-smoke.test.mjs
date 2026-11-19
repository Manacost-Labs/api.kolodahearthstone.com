import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
// Every statistics module must open without the app error screen. Registry = snapshot of analytics_module_registry().
const origin = process.env.PANEL_TEST_ORIGIN || 'http://127.0.0.1:4180';
const cookie = 'koloda_admin=fixture-session';
const registry = JSON.parse(
  readFileSync(new URL('./fixtures/analytics-registry.json', import.meta.url), 'utf8'),
);
const crash = 'Сервер данных панели не ответил';
// Server rendering never contains the error screen itself: a failed render leaves the React error marker `$RX(`.
const crashed = html => html.includes(crash) || html.includes('$RX(');
const has = (html, pattern) => pattern.test(text(html));
const page = async search => {
  const response = await fetch(origin + '/?action=analytics' + search, { headers: { Cookie: cookie } });
  return { status: response.status, html: await response.text() };
};
const text = html =>
  html
    .replace(/<[^>]+>/g, ' ')
    .replace(/&quot;/g, '"')
    .replace(/&#x27;/g, "'");

test('the registry snapshot lists all 13 modules', () => {
  assert.equal(Object.keys(registry.modules).length, 13);
});

for (const [key, definition] of Object.entries(registry.modules)) {
  if (key === 'card') continue;
  test('stats=' + key + ' opens with its title and no crash screen', async () => {
    const { status, html } = await page('&stats=' + key);
    assert.equal(status, 200);
    assert.ok(html.includes('<h1>' + definition.title + '</h1>'), 'heading ' + definition.title);
    assert.ok(!crashed(html), 'crash screen');
  });
}

test('stats=card without a name shows the card page with a hint and no crash screen', async () => {
  const { status, html } = await page('&stats=card');
  assert.equal(status, 200);
  assert.ok(html.includes('<h1>Статистика карты</h1>'), 'heading');
  assert.ok(has(html, /Введите название карты/), 'hint');
  assert.ok(/name="card_name"/.test(html), 'card field');
  assert.ok(!crashed(html), 'crash screen');
});

test('stats=card&q=Матриарх (inspector button) loads that card', async () => {
  const { status, html } = await page('&stats=card&q=' + encodeURIComponent('Матриарх'));
  assert.equal(status, 200);
  assert.ok(html.includes('<h1>Статистика карты: Матриарх</h1>'), 'heading with card');
  assert.ok(
    /name="card_name"[^>]*value="Матриарх"|value="Матриарх"[^>]*name="card_name"/.test(html),
    'field filled',
  );
  assert.ok(!has(html, /Введите название карты/), 'no hint');
  assert.ok(!crashed(html), 'crash screen');
});

test('a module that answers 502 keeps its page, the message and the menu', async () => {
  const { status, html } = await page('&stats=arena&fixture_fail=1');
  assert.equal(status, 200);
  assert.ok(has(html, /Не удалось загрузить статистику\./), 'message');
  assert.ok(/role="alert"/.test(html), 'alert');
  assert.ok(html.includes('<h1>Арена</h1>'), 'module heading');
  assert.ok(has(html, /Статистика карты/), 'module menu stays');
  assert.ok(!crashed(html), 'crash screen');
});
