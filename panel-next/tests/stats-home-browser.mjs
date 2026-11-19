// «Сводка» (#110): four cards, dates, three clicks at most into a mode, a failing module on its own card.
// Runs like stats-browser.mjs: PANEL_TEST_ORIGIN=http://127.0.0.1:4180 PANEL_FIXTURE_ORIGIN=http://127.0.0.1:18768 node tests/stats-home-browser.mjs
export default async function verify(page, root, fixture) {
  const checks = {};
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const ensure = (condition, message) => {
    if (!condition) throw Error(message);
  };
  const card = key => page.locator(`.stats-home__card[data-card="${key}"]`);
  const open = async (width = 1440) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(root + '/?action=analytics');
    await page.getByRole('heading', { level: 1, name: 'Статистика · Сводка' }).waitFor();
    await page.locator('.stats-home__card').first().waitFor();
  };
  const fail = modules => page.request.get(fixture + '/test/fail?modules=' + modules.join(','));

  // 1. Four cards, each with a date and three rows; 2×2 on the desktop.
  await open(1440);
  ensure((await page.locator('.stats-home__card').count()) === 4, 'Four cards');
  for (const key of ['standard', 'wild', 'bg', 'arena']) {
    const text = await card(key).innerText();
    ensure(/Данные на \d{1,2} \S+ 2026/u.test(text), key + ': no data date: ' + text);
    ensure((await card(key).locator('.stats-home__row').count()) === 3, key + ': not three rows');
    ensure(!/NaN|undefined/.test(text), key + ': broken number');
  }
  const boxes = await page
    .locator('.stats-home__card')
    .evaluateAll(items =>
      items.map(item => item.getBoundingClientRect()).map(({ x, y }) => [Math.round(x), Math.round(y)]),
    );
  ensure(
    boxes[0][1] === boxes[1][1] && boxes[2][1] === boxes[3][1] && boxes[2][1] > boxes[0][1],
    '2×2 grid: ' + boxes,
  );
  ensure(boxes[0][0] === boxes[2][0] && boxes[1][0] > boxes[0][0], '2×2 columns: ' + boxes);
  // Numbers stay on one line: a row is as tall as its grid says (min 48 px, two name lines at most).
  const tall = await page
    .locator('.stats-home__row')
    .evaluateAll(rows => rows.filter(row => row.getBoundingClientRect().height > 70).length);
  ensure(tall === 0, 'A row wrapped');
  ensure((await card('standard').locator('img').count()) === 3, 'Class icons in Standard');
  ensure((await card('bg').locator('img').count()) === 0, 'Battlegrounds heroes have no class icon');
  checks.fourCards = true;

  // 2. From the entrance to the mode: the menu item, then a card: no more than three clicks.
  await page.goto(root + '/?action=list');
  const sidebar = page.getByRole('navigation', { name: 'Разделы панели' });
  await sidebar.getByRole('button', { name: 'Статистика', exact: true }).click(); // 1
  await sidebar.getByRole('link', { name: 'Сводка', exact: true }).click(); // 2
  await page.getByRole('heading', { level: 1, name: 'Статистика · Сводка' }).waitFor();
  await card('wild').click(); // 3
  await page.waitForFunction(
    () => location.search.includes('stats=meta') && location.search.includes('format=wild'),
  );
  await page.getByRole('heading', { level: 1, name: 'Мета Standard / Wild' }).waitFor();
  const format = page.getByRole('combobox', { name: 'Формат', exact: true });
  await format.waitFor();
  ensure(
    /Вольный|Wild/.test(await format.innerText()),
    'The meta page does not show the Wild format: ' + (await format.innerText()),
  );
  checks.threeClicks = true;
  for (const [key, query, heading] of [
    ['standard', 'stats=meta', 'Мета Standard / Wild'],
    ['bg', 'stats=bg_heroes', null],
    ['arena', 'stats=arena', null],
  ]) {
    await open(1440);
    await card(key).click();
    await page.waitForFunction(q => location.search.includes(q), query);
    await page.getByRole('heading', { level: 1 }).waitFor();
    if (heading) await page.getByRole('heading', { level: 1, name: heading }).waitFor();
  }
  checks.cardsOpenModes = true;

  // 3. Tab reaches the four cards in order with a visible focus.
  await open(1440);
  await page.locator('#panel-main').evaluate(main => main.querySelector('.stats-home__card')?.focus());
  const focused = [];
  for (let i = 0; i < 4; i++) {
    focused.push(await page.evaluate(() => document.activeElement?.getAttribute('data-card')));
    await page.keyboard.press('Tab');
  }
  ensure(
    JSON.stringify(focused) === JSON.stringify(['standard', 'wild', 'bg', 'arena']),
    'Tab order: ' + focused,
  );
  checks.keyboard = true;

  // 4. One module fails: its card says so, the other three work, the link stays.
  await fail(['bg_heroes']);
  try {
    await open(1440);
    const failed = card('bg');
    await failed.getByRole('status').waitFor();
    ensure(/Не удалось загрузить/.test(await failed.innerText()), 'Error text on the card');
    ensure(
      (await failed.getAttribute('href')).includes('stats=bg_heroes'),
      'The failed card is still a link',
    );
    for (const key of ['standard', 'wild', 'arena'])
      ensure((await card(key).locator('.stats-home__row').count()) === 3, key + ' lost its rows');
    ensure(
      (await page.locator('#panel-main [role="alert"]').count()) === 0,
      'A page-level alert for a card error',
    );
    checks.oneFailure = true;
  } finally {
    await fail([]);
  }

  // 5. Phone and narrow desktop: one column on 390, nothing scrolls sideways.
  for (const width of [1280, 390]) {
    await open(width);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    ensure(overflow <= 0, `Sideways scroll at ${width}: ${overflow}`);
  }
  const phone = await page
    .locator('.stats-home__card')
    .evaluateAll(items => items.map(item => Math.round(item.getBoundingClientRect().x)));
  ensure(new Set(phone).size === 1, 'One column on a phone: ' + phone);
  checks.noSidewaysScroll = true;

  ensure(pageErrors.length === 0, 'Uncaught browser errors: ' + pageErrors.join('; '));
  checks.noUncaughtErrors = true;
  await page.evaluate(value => {
    window.uxQaResults = value;
  }, checks);
  return checks;
}

// Optional local browser gate; uses fixture sessions and blocks all remote requests.
if (process.argv[1] === new URL(import.meta.url).pathname) {
  const { spawnSync } = await import('node:child_process');
  const origin = process.env.PANEL_TEST_ORIGIN || 'http://127.0.0.1:4180';
  const fixture = process.env.PANEL_FIXTURE_ORIGIN || 'http://127.0.0.1:18768';
  for (const value of [origin, fixture])
    if (!['127.0.0.1', 'localhost'].includes(new URL(value).hostname))
      throw Error('Browser QA requires a local fixture origin.');
  const cli = process.env.PLAYWRIGHT_CLI || 'playwright-cli';
  const session = process.env.PLAYWRIGHT_SESSION || 'stats-home-regression';
  const command = (...args) => {
    const result = spawnSync(cli, ['-s=' + session, ...args], { encoding: 'utf8' });
    if (result.status !== 0) throw Error(result.stdout + '\n' + result.stderr);
    return result.stdout;
  };
  try {
    command('open', 'about:blank');
    const code = `async page => {
      await page.context().addCookies([{name:'koloda_admin',value:'fixture-session',url:${JSON.stringify(origin)}}]);
      await page.route('https://**',route=>route.abort());
      await (${verify.toString()})(page,${JSON.stringify(origin)},${JSON.stringify(fixture)});
    }`;
    command('run-code', code);
    console.log(command('eval', '() => window.uxQaResults'));
  } finally {
    command('close');
  }
}
