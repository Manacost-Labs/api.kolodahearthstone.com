export default async function verify(page, root, output) {
  const checks = {};
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const ensure = (condition, message) => {
    if (!condition) throw Error(message);
  };
  const settled = () =>
    page.waitForFunction(
      () => document.querySelector('.catalog-results')?.getAttribute('aria-busy') === 'false',
    );
  const load = async query => {
    await page.goto(root + '/?' + query);
    await settled();
  };
  await page.setViewportSize({ width: 1440, height: 760 });
  await load('view=grid');
  await page.evaluate(() => {
    window.uxHeader = document.querySelector('.site-header');
    window.uxInput = document.querySelector('input[name=q]');
  });
  const search = page.getByRole('searchbox', { name: 'Поиск карт' });
  await search.fill('BG_FIXTURE_1');
  await page.waitForFunction(
    () => document.querySelector('.catalog-results')?.getAttribute('aria-busy') === 'true',
  );
  ensure((await page.locator('.card-tile').count()) === 8, 'Old results disappeared during refresh');
  ensure(
    await page.evaluate(() => document.activeElement === window.uxInput),
    'Search lost focus while refreshing',
  );
  await search.fill('BG_FIXTURE_2');
  await settled();
  ensure((await search.inputValue()) === 'BG_FIXTURE_2', 'Old response replaced newer search input');
  ensure(
    await page.evaluate(() => window.uxInput === document.querySelector('input[name=q]')),
    'Search node was remounted',
  );
  ensure(new URL(page.url()).searchParams.get('q') === 'BG_FIXTURE_2', 'Latest search did not win');
  checks.searchAndStaleResponses = true;
  await load('view=grid');
  await page.getByRole('combobox', { name: 'Все уровни' }).selectOption('2');
  await page.getByRole('combobox', { name: 'Все типы' }).selectOption('beast');
  await settled();
  ensure(
    new URL(page.url()).searchParams.get('tier') === '2' &&
      new URL(page.url()).searchParams.get('creature_type') === 'beast',
    'Rapid filter changes overwrote one another',
  );
  checks.rapidFilters = true;
  await load('view=grid');
  const searches = [];
  const track = request => {
    const url = new URL(request.url());
    if (url.searchParams.get('q') === 'BG_FIXTURE_24' && url.searchParams.has('_rsc'))
      searches.push(url.href);
  };
  page.on('request', track);
  await search.fill('BG_FIXTURE_24');
  await search.press('Enter');
  await settled();
  await page.waitForTimeout(450);
  page.off('request', track);
  ensure(searches.length === 1, 'Enter left a duplicate debounced request: ' + searches.length);
  checks.enterCancelsDebounce = true;
  await load('view=grid');
  await page
    .getByRole('navigation', { name: 'Страницы каталога' })
    .getByRole('button', { name: '2', exact: true })
    .click();
  await settled();
  ensure(new URL(page.url()).searchParams.get('page') === '2', 'Pagination did not navigate');
  await page.goBack();
  await settled();
  ensure(!new URL(page.url()).searchParams.has('page'), 'Browser Back did not restore the previous page');
  checks.history = true;
  await page
    .getByRole('navigation', { name: 'Страницы каталога' })
    .getByRole('button', { name: '2', exact: true })
    .click();
  await settled();
  await search.fill('UNSENT_SEARCH');
  await page.goBack();
  await settled();
  await page.waitForTimeout(750);
  ensure(
    !new URL(page.url()).searchParams.has('q') && !new URL(page.url()).searchParams.has('page'),
    'An old debounce overrode browser Back',
  );
  checks.backCancelsDebounce = true;
  await page
    .getByRole('navigation', { name: 'Страницы каталога' })
    .getByRole('button', { name: '2', exact: true })
    .click();
  await settled();
  const originalUrl = page.url();
  await page.evaluate(() => {
    window.uxHeader = document.querySelector('.site-header');
    window.scrollTo(0, 380);
  });
  const originalY = await page.evaluate(() => window.scrollY);
  await page
    .getByRole('navigation', { name: 'Основные разделы' })
    .getByRole('link', { name: 'Статистика' })
    .click();
  await page.waitForFunction(
    () => location.search.includes('action=analytics') && document.querySelector('.analytics-layout'),
  );
  ensure(
    await page.evaluate(() => window.uxHeader === document.querySelector('.site-header')),
    'Header remounted between sections',
  );
  await page
    .getByRole('navigation', { name: 'Основные разделы' })
    .getByRole('link', { name: 'Каталог' })
    .click();
  await settled();
  await page.waitForTimeout(100);
  ensure(page.url() === originalUrl, 'Returning to catalogue lost its URL');
  ensure(
    Math.abs((await page.evaluate(() => window.scrollY)) - originalY) < 3,
    'Returning to catalogue lost scroll position',
  );
  checks.sectionReturn = { scroll: originalY, headerStable: true };
  await search.fill('UNSENT_SEARCH');
  await page
    .getByRole('navigation', { name: 'Основные разделы' })
    .getByRole('link', { name: 'Статистика' })
    .click();
  await page.waitForFunction(
    () => location.search.includes('action=analytics') && document.querySelector('.analytics-layout'),
  );
  await page.waitForTimeout(750);
  ensure(
    new URL(page.url()).searchParams.get('action') === 'analytics',
    'A search timer overrode section navigation',
  );
  await page
    .getByRole('navigation', { name: 'Основные разделы' })
    .getByRole('link', { name: 'Каталог' })
    .click();
  await settled();
  checks.sectionChangeCancelsDebounce = true;
  await page.locator('.card-tile').first().click();
  await page.locator('dialog[open]').waitFor();
  const width = await page.evaluate(() => document.documentElement.clientWidth);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('dialog[open]'));
  ensure(
    await page.evaluate(() => document.activeElement.classList.contains('card-tile')),
    'Modal did not restore focus',
  );
  ensure(
    width === (await page.evaluate(() => document.documentElement.clientWidth)),
    'Modal changed page width',
  );
  checks.modalFocusAndWidth = true;
  for (const [name, selector] of [
    ['Плитки', '.deck-tile'],
    ['Таблица', 'tbody tr'],
    ['Карточки', '.card-tile'],
  ]) {
    await page.getByRole('button', { name, exact: true }).click();
    await settled();
    ensure((await page.locator(selector).count()) > 0, 'View did not change: ' + name);
    const view = new URL(page.url()).searchParams.get('view');
    await page.goto(root + '/');
    await settled();
    ensure(new URL(page.url()).searchParams.get('view') === view, 'Remembered view did not load: ' + view);
  }
  checks.allViewModesRemembered = true;
  await search.fill('НЕТ_ТАКОЙ_КАРТЫ');
  await settled();
  // Wait until the debounce has actually applied, not merely until the old screen is idle.
  await page.waitForFunction(() => location.search.includes('q=') && document.querySelector('.empty'));
  ensure(await page.locator('.empty').isVisible(), 'Missing empty state');
  await search.fill('М');
  await page.waitForFunction(
    () =>
      new URLSearchParams(location.search).get('q') === 'М' &&
      document.querySelector('.catalog-results').getAttribute('aria-busy') === 'false',
  );
  ensure((await page.locator('.card-tile').count()) > 0, 'Single-character search was ignored');
  checks.emptyAndSingleCharacterSearch = true;
  await page.evaluate(() => localStorage.setItem('hsDataTheme-v2', 'dark'));
  await load('view=tiles');
  ensure(
    await page.evaluate(() => document.documentElement.dataset.theme === 'dark'),
    'Saved theme not applied',
  );
  await page.screenshot({ path: output + '/desktop.png' });
  for (const width of [320, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 800 });
    ensure(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      'Horizontal page overflow at ' + width,
    );
  }
  checks.responsive = true;
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.locator('.deck-tile').first().click();
  await page.locator('dialog[open]').waitFor();
  ensure(
    (await page
      .locator('dialog')
      .first()
      .evaluate(el => getComputedStyle(el).animationName)) === 'none',
    'Reduced motion still animates the dialog',
  );
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => !document.querySelector('dialog[open]'));
  checks.reducedMotion = true;
  await page.goto(root + '/?view=tiles&q=__fixture_error__');
  await page.getByRole('heading', { name: 'Не удалось загрузить панель' }).waitFor();
  ensure(await page.locator('.site-header').isVisible(), 'Server error removed the workspace header');
  await page.getByRole('button', { name: 'Повторить', exact: true }).click();
  await settled();
  ensure(await page.locator('.empty').isVisible(), 'Retry did not recover after a server error');
  checks.errorAndRetry = true;
  await load('view=tiles');
  ensure(pageErrors.length === 0, 'Uncaught browser errors: ' + pageErrors.join('; '));
  checks.noUncaughtErrors = true;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: output + '/mobile.png' });
  await page.evaluate(value => {
    window.catalogQaResults = value;
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
  const output = process.env.PANEL_QA_OUTPUT || '/tmp/koloda-panel-ux-browser';
  await mkdir(output, { recursive: true });
  const cli = process.env.PLAYWRIGHT_CLI || 'playwright-cli';
  const session = 'catalog-regression';
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
      await (${verify.toString()})(page,${JSON.stringify(origin)},${JSON.stringify(output)});
    }`;
    command('run-code', code);
    console.log(command('eval', '() => window.catalogQaResults'));
  } finally {
    command('close');
  }
}
