// Statistics filters, pager, KPI and number formats on the 13 modules, asserted through roles and visible text.
export default async function verify(page, root) {
  const checks = {};
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const ensure = (condition, message) => {
    if (!condition) throw Error(message);
  };
  const modules = [
    'meta',
    'archetypes',
    'hsguru_archetypes',
    'decks',
    'constructed_cards',
    'bg_heroes',
    'bg_minions',
    'arena',
    'arena_cards',
    'card',
    'patches',
    'overview',
    'providers',
  ];
  const open = async (query, width = 1440) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto(root + '/?action=analytics&stats=' + query);
    await page.getByRole('heading', { level: 1 }).waitFor();
    await page.waitForFunction(() => document.querySelector('.analytics-main')?.getAttribute('data-module'));
    await page.locator('.stats-body[data-busy="false"], .empty').first().waitFor();
  };
  const settled = () => page.waitForFunction(() => !document.querySelector('.stats-body[data-busy="true"]'));
  const visible = () => page.locator('#panel-main').innerText();
  const percentShape = /^\d{1,3}( \d{3})*,\d{1,2} %$/u;
  const forbidden = [
    /past_day/,
    /\blegend\b/,
    /STANDARD/,
    /RANKED_/,
    /Записей/,
    /Смещение/,
    /<b>/,
    /Options/,
    /Option hints/,
    /option_hints/,
  ];

  // 1. Every module: no raw slugs, no paging fields, every share in one shape.
  for (const key of modules) {
    await open(key + (key === 'card' ? '&card_name=Тестовая карта' : ''));
    const text = await visible();
    for (const word of forbidden) ensure(!word.test(text), `${key}: «${word}» in visible text`);
    if (key === 'overview') await page.locator('.reliability').waitFor();
    const fields = await page.locator('input[name="limit"], input[name="offset"]').count();
    ensure(fields === 0, key + ': limit/offset fields are shown');
    const shares = await page
      .locator('.analytics-main td, .stats-kpis, .reliability')
      .evaluateAll(cells => cells.flatMap(cell => cell.textContent.match(/[\d., ]+\s?%/g) ?? []));
    // The reliability tiles sit outside the table: make sure their shares are actually read.
    if (key === 'overview')
      ensure(
        /\d\s?%/.test(await page.locator('.reliability').innerText()),
        'overview: no shares read from the reliability block',
      );
    for (const share of shares)
      ensure(percentShape.test(share.trim()), `${key}: share «${share}» is not in the common shape`);
    ensure(!/\d\.\d+\s?%/.test(shares.join(' ')), key + ': 7.98% style share');
  }
  checks.thirteenModulesClean = true;

  // 2. Meta: lists with Russian wording, the current patch among the periods.
  await open('meta');
  for (const name of ['Формат', 'Ранг', 'Период', 'Минимум игр'])
    ensure(await page.getByRole('combobox', { name, exact: true }).isVisible(), name + ' is not a list');
  const period = page.getByRole('combobox', { name: 'Период', exact: true });
  ensure((await period.innerText()).trim() === '24 часа', 'Default period: ' + (await period.innerText()));
  await period.click();
  const labels = (await page.getByRole('option').allInnerTexts()).map(text => text.split('\n')[0].trim());
  ensure(labels.includes('Патч 37.0.2') && labels.includes('7 дней'), 'Period list: ' + labels);
  ensure(labels.includes('Чёрная империя') && labels.includes('Баны'), 'Period list: ' + labels);
  await page.getByRole('option', { name: '7 дней', exact: true }).click();
  await settled();
  await page.waitForFunction(() => location.search.includes('period=past_week'));
  checks.metaFilters = true;

  // 2b. The hint of the meta period list comes from the answer's `option_hints`.
  await page.getByRole('combobox', { name: 'Период', exact: true }).click();
  const hinted = await page.getByRole('option', { name: /Патч 37\.0\.2/ }).innerText();
  ensure(/Текущий патч/.test(hinted), 'The current patch has no hint: ' + hinted);
  await page.keyboard.press('Escape');
  checks.periodHints = true;

  // 3. KPI: date, games, archetypes, without echoing the chosen filters.
  const kpi = (await page.locator('.stats-kpis').innerText()).replace(/\s+/g, ' ');
  for (const label of ['Данные на', 'Игр в выборке', 'Архетипов'])
    ensure(kpi.includes(label), `KPI «${label}» is missing: ${kpi}`);
  for (const echo of ['Формат', 'Ранг', 'Период', 'Стандарт', '7 дней'])
    ensure(!kpi.includes(echo), `KPI repeats the filter «${echo}»: ${kpi}`);
  ensure(!(await visible()).includes('Обновлено:'), 'The «Обновлено» line repeats «Данные на»');
  checks.metaKpi = true;

  // 4. The old PHP answer (no keyed summary): the panel counts the KPI itself.
  await open('constructed_cards');
  const legacy = (await page.locator('.stats-kpis').innerText()).replace(/\s+/g, ' ');
  for (const label of ['Данные на', 'Игр в выборке', 'Карт'])
    ensure(legacy.includes(label), `fallback KPI «${label}» is missing: ${legacy}`);
  checks.fallbackKpi = true;

  // 5. Reset only when something differs from the defaults.
  await open('meta');
  const reset = page.getByRole('button', { name: 'Сбросить' });
  ensure(!(await reset.isVisible()), 'Reset is shown at the defaults');
  await page.getByRole('combobox', { name: 'Формат', exact: true }).click();
  await page.getByRole('option', { name: 'Вольный', exact: true }).click();
  await reset.waitFor({ state: 'visible' });
  await reset.click();
  await page.waitForFunction(() => !location.search.includes('format='));
  checks.resetOnlyWhenChanged = true;

  // 6. Pager: «1–50 из 812», page in the URL, back to 1 after a filter change.
  await open('decks');
  const caption = page.locator('.stats-pager__caption');
  ensure((await caption.innerText()) === '1–25 из 812', 'Pager caption: ' + (await caption.innerText()));
  const firstRow = () => page.locator('.analytics-main tbody tr').first().innerText();
  const before = await firstRow();
  await page.locator('.stats-pager__full').getByRole('button', { name: 'Страница 2', exact: true }).click();
  await page.waitForFunction(() => location.search.includes('page=2'));
  await settled();
  await page.waitForFunction(
    () => document.querySelector('.stats-pager__caption')?.textContent === '26–50 из 812',
  );
  ensure((await firstRow()) !== before, 'Page 2 shows the same rows as page 1');
  const search = page.getByRole('searchbox', { name: 'Поиск', exact: true });
  await search.fill('Aggro');
  await search.press('Enter');
  await page.waitForFunction(() => location.search.includes('q=Aggro') && !location.search.includes('page='));
  checks.pager = true;

  // 6b. A direct address past the last page (or past the backend offset limit): the page is honest.
  for (const far of ['402', '99999', '40']) {
    await open('decks&page=' + far);
    await page.getByRole('heading', { name: 'Такой страницы нет' }).waitFor();
    ensure((await page.locator('.stats-pager__caption').count()) === 0, 'A range is printed for page ' + far);
    ensure((await page.locator('table').count()) === 0, 'Rows are shown for page ' + far);
    await page.getByRole('button', { name: 'Перейти на последнюю страницу' }).click();
    await page.waitForFunction(() => location.search.includes('page=33'));
    await page.waitForFunction(
      () => document.querySelector('.stats-pager__caption')?.textContent === '801–812 из 812',
    );
  }
  checks.pageBeyondRange = true;

  // 6c. Deck filters: class and format are lists starting with «Все»; the source stays a typed field.
  await open('decks');
  const classList = page.getByRole('combobox', { name: 'Класс', exact: true });
  const formatList = page.getByRole('combobox', { name: 'Формат', exact: true });
  ensure(
    (await classList.innerText()).trim() === 'Все',
    'Class list starts at ' + (await classList.innerText()),
  );
  ensure(
    (await formatList.innerText()).trim() === 'Все',
    'Format list starts at ' + (await formatList.innerText()),
  );
  await classList.click();
  const classLabels = (await page.getByRole('option').allInnerTexts()).map(text =>
    text.split('\n')[0].trim(),
  );
  ensure(
    classLabels[0] === 'Все' &&
      classLabels.includes('Охотник на демонов') &&
      classLabels.includes('Рыцарь смерти'),
    'Class list: ' + classLabels,
  );
  ensure(!classLabels.some(label => /^[A-Z_]+$/.test(label)), 'A class slug in the list: ' + classLabels);
  await page.getByRole('option', { name: 'Маг', exact: true }).click();
  await page.waitForFunction(() => location.search.includes('class_name=MAGE'));
  await settled();
  ensure(
    (await page.getByRole('combobox', { name: 'Класс', exact: true }).innerText()).trim() === 'Маг',
    'Class not kept',
  );
  await page.getByRole('combobox', { name: 'Класс', exact: true }).click();
  await page.getByRole('option', { name: 'Все', exact: true }).click();
  await page.waitForFunction(() => !location.search.includes('class_name='));
  ensure(
    (await page.getByRole('textbox', { name: 'Источник', exact: true }).count()) === 1,
    'Source is not a field',
  );
  checks.deckFilterLists = true;

  // 7. Search by Enter; one field, no «Применить».
  ensure(!(await page.getByRole('button', { name: 'Применить' }).count()), '«Применить» is back');
  checks.searchByEnter = true;

  // 8. The card module without a card: the field and the hint, no table.
  await open('card');
  const field = page.getByRole('searchbox', { name: 'Карта', exact: true });
  await field.waitFor();
  await page.getByRole('heading', { name: 'Выберите карту' }).waitFor();
  ensure((await page.locator('table').count()) === 0, 'A table without a card');
  await field.fill('Тестовая карта');
  await field.press('Enter');
  await page.waitForFunction(() => location.search.includes('card_name='));
  await page.locator('.analytics-main tbody tr').first().waitFor();
  checks.cardWithoutName = true;

  // 8b. A failed load keeps the period a list with Russian wording, never a slug in a text field.
  await open('meta&fixture_fail=1');
  const failedPeriod = page.getByRole('combobox', { name: 'Период', exact: true });
  ensure(
    (await failedPeriod.innerText()).trim() === '24 часа',
    'Period after a failure: ' + (await failedPeriod.innerText()),
  );
  ensure(!(await page.locator('input[name="period"]').count()), 'The period is a free field after a failure');
  const failedText = await visible();
  for (const word of forbidden) ensure(!word.test(failedText), `failed meta: «${word}» in visible text`);
  await failedPeriod.click();
  const failedLabels = (await page.getByRole('option').allInnerTexts()).map(text =>
    text.split('\n')[0].trim(),
  );
  ensure(
    failedLabels.includes('7 дней') && !failedLabels.some(label => /_/.test(label)),
    'Failed period list: ' + failedLabels,
  );
  await page.keyboard.press('Escape');
  checks.failedLoadKeepsLists = true;

  // 9. The phone and the narrow desktop: nothing scrolls sideways, the pager turns compact.
  for (const width of [1280, 390]) {
    for (const key of modules) {
      await open(key + (key === 'card' ? '&card_name=Тестовая карта' : ''), width);
      const overflow = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        filters: document.querySelector('.stats-filters')
          ? document.querySelector('.stats-filters').scrollWidth -
            document.querySelector('.stats-filters').clientWidth
          : 0,
      }));
      ensure(overflow.page <= 0 && overflow.filters <= 0, `${key} at ${width}: ${JSON.stringify(overflow)}`);
    }
  }
  await open('decks', 390);
  ensure(await page.locator('.stats-pager__compact').isVisible(), 'The pager is not compact on a phone');
  ensure(!(await page.locator('.stats-pager__full').isVisible()), 'Both pagers show on a phone');
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
  const { mkdir } = await import('node:fs/promises');
  const origin = process.env.PANEL_TEST_ORIGIN || 'http://127.0.0.1:4182';
  if (!['127.0.0.1', 'localhost'].includes(new URL(origin).hostname))
    throw Error('Browser QA requires a local fixture origin.');
  const output = process.env.PANEL_QA_OUTPUT || '/tmp/koloda-panel-stats-browser';
  await mkdir(output, { recursive: true });
  const cli = process.env.PLAYWRIGHT_CLI || 'playwright-cli';
  const session = 'stats-regression';
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
      await (${verify.toString()})(page,${JSON.stringify(origin)});
    }`;
    command('run-code', code);
    console.log(command('eval', '() => window.uxQaResults'));
  } finally {
    command('close');
  }
}
