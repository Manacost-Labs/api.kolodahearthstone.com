// Browser scenario for the card language switch (E23 S8). Needs a running panel on the local
// fixture (see README): PANEL_TEST_ORIGIN=http://127.0.0.1:4180 node tests/card-locale-browser.mjs
// The public card-catalog API is answered from fixtures by `page.route`; nothing leaves the machine.
// PANEL_LOCALE_LIVE=1: only BE_036 is proxied (GET) to the public API, to take screenshots of real text.
// The playwright-cli `run-code` sandbox has no URL, URLSearchParams, Buffer or setTimeout: this function uses
// only `page` methods, plain string parsing and JSON.
export default async function verify(page, root, output, fixtures, live) {
  const checks = {};
  try {
    const pageErrors = [];
    const consoleErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('console', message => {
      if (message.type() === 'error') consoleErrors.push(message.text());
    });
    const ensure = (condition, message) => {
      if (!condition) throw Error(message);
    };
    const equal = (actual, expected, message) =>
      ensure(
        actual === expected,
        `${message}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
      );

    // ---- the fake public API -------------------------------------------------------------------
    const requests = []; // { url, cookie }
    const behaviour = { failServer: false };
    let inflight = 0;
    let peak = 0;
    const names = { plPL: 'PL', esES: 'ES', enUS: 'EN' };
    const synthetic = (locale, id, dbf, extra = {}) => ({
      data: {
        card_id: id,
        dbf,
        name: { ru: 'x', en: 'x' },
        localized: {
          locale,
          requested_locale: locale,
          fallback: false,
          name: `${names[locale]} ${id}`,
          text: `<b>Okrzyk bojowy:</b> Zadaj 21 |4(raz,razy,razy).`,
          flavor: `Flavor ${id}`,
          ...extra,
        },
      },
    });
    await page.context().route('**/uploads/test-render.webp', route =>
      route.fulfill({
        contentType: 'image/svg+xml',
        body: '<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="#888"/></svg>',
      }),
    );
    await page.context().route('**/api/v1/card-catalog/**', async route => {
      const request = route.request();
      const href = request.url();
      const target = href.slice(href.indexOf('/api/v1/'));
      const pathname = target.split('?')[0];
      const headers = await request.allHeaders();
      requests.push({ url: target, cookie: headers.cookie || '' });
      inflight += 1;
      peak = Math.max(peak, inflight);
      await page.waitForTimeout(25);
      inflight -= 1;
      const locale = (/[?&]locale=([A-Za-z]+)/.exec(target) || [])[1];
      const byDbf = pathname.includes('/card-catalog/dbf/');
      const key = decodeURIComponent(pathname.split('/').pop());
      const json = (body, status = 200) =>
        route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (key === 'BE_036') {
        if (live) {
          const real = await route.fetch({
            url: 'https://api.kolodahearthstone.com' + target,
          });
          return route.fulfill({ response: real });
        }
        return json(fixtures[`be036-${locale}`]);
      }
      const number = Number(byDbf ? key - 69000 + 1 : key.replace('BG_FIXTURE_', ''));
      const id = byDbf ? 'BG_FIXTURE_' + number : key;
      const dbf = 69000 + number - 1;
      if (number === 3) return json({ error: { code: 'not_found' } }, 404); // not in the catalogue at all
      if (number === 5 && !byDbf) return json({ error: { code: 'not_found' } }, 404); // found by dbf
      if (number === 4 && behaviour.failServer) return json({ error: { code: 'internal' } }, 500);
      if (number === 2)
        return json(
          synthetic('enUS', id, dbf, {
            requested_locale: locale,
            fallback: true,
            name: 'English fallback 2',
          }),
        );
      if (number === 6) return json(synthetic(locale, id, dbf, { render_url: '/uploads/test-render.webp' }));
      return json(synthetic(locale, id, dbf));
    });

    // ---- helpers -------------------------------------------------------------------------------
    const settled = () =>
      page.waitForFunction(
        () => document.querySelector('.catalog-results')?.getAttribute('aria-busy') === 'false',
      );
    const load = async query => {
      // A navigation still settling from the previous step (history.back) can abort this one: try again.
      await page.goto(root + '/?' + query).catch(() => page.goto(root + '/?' + query));
      await settled();
    };
    const param = name => page.evaluate(n => new URL(location.href).searchParams.get(n), name);
    const choose = async (label, scope = page) =>
      scope.getByRole('radio', { name: label, exact: true }).click();
    const catalogRequests = () => requests.length;
    const duplicates = () => {
      const seen = new Set();
      for (const { url } of requests) {
        if (seen.has(url)) return url;
        seen.add(url);
      }
      return '';
    };
    const gridName = id =>
      page
        .locator('.card-tile', { has: page.locator('code', { hasText: new RegExp('^' + id + '$') }) })
        .locator('.card-name');

    await page.setViewportSize({ width: 1440, height: 900 });

    // ---- Russian: no requests, no parameter -----------------------------------------------------
    await load('card_type=constructed&view=grid');
    equal(await param('lang'), null, 'Russian has no lang parameter');
    await page.waitForTimeout(300);
    equal(catalogRequests(), 0, 'Russian made requests to the card catalogue');
    equal(await gridName('BE_036').innerText(), 'МАТРИАРХ', 'panel name');
    const group = page.getByRole('radiogroup', { name: 'Язык карт' });
    equal(await group.getByRole('radio').count(), 4, 'four languages');
    for (const name of ['Русский', 'Английский', 'Польский', 'Испанский'])
      ensure(await group.getByRole('radio', { name, exact: true }).isVisible(), 'language option ' + name);
    checks.russianNoRequests = true;

    // ---- Polish in the cards view ----------------------------------------------------------------
    const historyBefore = await page.evaluate(() => history.length);
    await choose('Польский');
    await page.waitForFunction(() =>
      document.querySelector('.card-name')?.textContent?.includes('M.A.T.K.A.'),
    );
    equal(await param('lang'), 'pl', 'lang in the address');
    equal(await page.evaluate(() => history.length), historyBefore, 'switching added a history entry');
    const be = gridName('BE_036');
    equal((await be.innerText()).trim(), 'M.A.T.K.A.', 'Polish name');
    equal(await be.getAttribute('lang'), 'pl', 'lang attribute of the name');
    await page.waitForTimeout(500);
    ensure(
      requests.every(r => r.url.includes('locale=plPL')),
      'only plPL requested',
    );
    ensure(
      requests.every(r => r.cookie === ''),
      'requests carried cookies (credentials must be omit)',
    );
    ensure(peak <= 6, 'more than 6 requests at once: ' + peak);
    equal(duplicates(), '', 'a request was repeated');
    equal(await gridName('BG_FIXTURE_7').innerText(), 'PL BG_FIXTURE_7', 'synthetic Polish name');
    // 404 by card id -> one retry by dbf; both 404 -> the panel name and a note
    ensure(
      requests.some(r => r.url.startsWith('/api/v1/card-catalog/dbf/69004?')),
      'no retry by dbf',
    );
    equal(await gridName('BG_FIXTURE_5').innerText(), 'PL BG_FIXTURE_5', 'name found by dbf');
    ensure(
      (await gridName('BG_FIXTURE_3').innerText()).includes('Нет в каталоге карт — показаны данные панели'),
      'missing note',
    );
    ensure(
      (await gridName('BG_FIXTURE_2').innerText()).includes('Нет перевода на польский — показан английский'),
      'fallback note',
    );
    equal(await gridName('BG_FIXTURE_2').getAttribute('lang'), 'en', 'a fallback name is English');
    await page.screenshot({ path: output + '/locale-grid-pl.png' });
    checks.gridPolish = true;

    // ---- the three views keep the language ---------------------------------------------------------
    const view = label =>
      page.getByRole('radiogroup', { name: 'Вид каталога' }).getByRole('radio', { name: label });
    await view('Таблица').click();
    await settled();
    equal(await param('lang'), 'pl', 'language lost on the table view');
    await page.waitForFunction(() =>
      [...document.querySelectorAll('.row-name strong')].some(el => el.textContent?.includes('M.A.T.K.A.')),
    );
    const row = page.locator('.row-name', { hasText: 'M.A.T.K.A.' }).first();
    equal(await row.locator('strong').getAttribute('lang'), 'pl', 'table name lang');
    equal((await row.locator('small').innerText()).trim(), 'МАТРИАРХ', 'Russian panel name under it');
    equal(await row.locator('small').getAttribute('lang'), 'ru', 'Russian name lang');
    await page.screenshot({ path: output + '/locale-table-pl.png' });
    await view('Плитки').click();
    await settled();
    equal(await param('lang'), 'pl', 'language lost on the tiles view');
    await page.waitForFunction(() =>
      [...document.querySelectorAll('.deck-tile__name')].some(el => el.textContent?.includes('M.A.T.K.A.')),
    );
    equal(
      await page.locator('.deck-tile', { hasText: 'M.A.T.K.A.' }).getAttribute('lang'),
      'pl',
      'tile lang',
    );
    await page.screenshot({ path: output + '/locale-tiles-pl.png' });
    await view('Карточки').click();
    await settled();
    equal(duplicates(), '', 'a request was repeated after switching views');
    checks.threeViews = true;

    // ---- page, filter, reset and search keep the language -------------------------------------------
    await page
      .getByRole('navigation', { name: 'Страницы каталога' })
      .getByRole('button', { name: 'Страница 2', exact: true })
      .click();
    await settled();
    equal(await param('page'), '2', 'page 2');
    equal(await param('lang'), 'pl', 'language lost on pagination');
    await page.getByRole('searchbox', { name: 'Поиск карт' }).fill('BG_FIXTURE_1');
    await settled();
    equal(await param('q'), 'BG_FIXTURE_1', 'search applied');
    equal(await param('lang'), 'pl', 'language lost on search');
    await load('card_type=constructed&view=grid&lang=pl');
    await page.getByRole('combobox', { name: 'Формат' }).click();
    await page.getByRole('option', { name: 'Стандартный', exact: true }).click();
    await settled();
    equal(await param('constructed_format'), 'standard', 'filter applied');
    equal(await param('lang'), 'pl', 'language lost on filter');
    await page.getByRole('link', { name: 'Сбросить', exact: true }).click();
    await settled();
    equal(await param('lang'), 'pl', 'language lost on «Сбросить»');
    equal(await param('constructed_format'), null, 'reset did not clear the filter');
    checks.stateKept = true;

    // ---- failures: one status line, Retry repeats only the failed request ---------------------------
    behaviour.failServer = true;
    requests.length = 0;
    await load('card_type=constructed&view=grid&lang=es');
    await page.getByText('Не удалось загрузить перевод для 1 карту').waitFor();
    equal(
      await page.locator('.locale-status[role="alert"], .locale-status [role="alert"]').count(),
      0,
      'role=alert is used',
    );
    equal(await gridName('BG_FIXTURE_4').innerText(), 'Рыбный следопыт', 'panel data stays');
    await page.screenshot({ path: output + '/locale-error.png' });
    behaviour.failServer = false;
    const before = requests.length;
    await page.getByRole('button', { name: 'Повторить' }).click();
    await page.getByText('Не удалось загрузить перевод').waitFor({ state: 'detached' });
    await page.waitForFunction(() =>
      [...document.querySelectorAll('.card-name')].some(el => el.textContent?.includes('ES BG_FIXTURE_4')),
    );
    equal(requests.length - before, 1, 'Retry repeated more than the failed request');
    equal(await gridName('BG_FIXTURE_4').innerText(), 'ES BG_FIXTURE_4', 'retried name');
    checks.errorAndRetry = true;

    // A failure that goes away by itself: leave the language and come back, the status line disappears.
    behaviour.failServer = true;
    await load('card_type=constructed&view=grid&lang=pl');
    await page.getByText('Не удалось загрузить перевод для 1 карту').waitFor();
    behaviour.failServer = false;
    const switcher = page.getByRole('radiogroup', { name: 'Язык карт' }).first();
    await choose('Русский', switcher);
    await choose('Польский', switcher);
    await page.waitForFunction(() =>
      [...document.querySelectorAll('.card-name')].some(el => el.textContent?.includes('PL BG_FIXTURE_4')),
    );
    // Not a race with the re-render after the switch: the name is already translated here.
    await page.waitForTimeout(300);
    equal(
      await page.getByText('Не удалось загрузить перевод').count(),
      0,
      'the status line outlived a successful reload',
    );
    checks.errorClearsAfterReload = true;

    const closeInspector = async () => {
      await page.keyboard.press('Escape');
      await page.getByRole('dialog').waitFor({ state: 'detached' });
    };

    // ---- inspector -----------------------------------------------------------------------------------
    await load('card_type=constructed&view=grid');
    await gridName('BE_036').click();
    const dialog = page.getByRole('dialog');
    await dialog.waitFor();
    const inspectorSwitch = dialog.getByRole('radiogroup', { name: 'Язык карт' });
    const title = () => dialog.locator('h2').innerText();
    const lines = () => dialog.locator('.inspector-text').innerText();
    const expectations = {
      Русский: ['МАТРИАРХ', null],
      Английский: ['M.O.T.H.E.R.', 'Battlecry:'],
      Польский: ['M.A.T.K.A.', 'Okrzyk bojowy:'],
      Испанский: ['MADRE', 'Grito de batalla:'],
    };
    for (const [label, [name, bold]] of Object.entries(expectations)) {
      await choose(label, inspectorSwitch);
      await page.waitForFunction(
        expected => document.querySelector('dialog h2')?.textContent === expected,
        name,
      );
      equal(await title(), name, 'inspector title in ' + label);
      const body = await lines();
      ensure(!/[<>]|\[x\]|\$\d/.test(body), 'service markers in the text: ' + body);
      if (bold)
        ensure(
          (await dialog.locator('.inspector-text strong', { hasText: bold }).count()) > 0,
          'no bold keyword in ' + label,
        );
      if (label !== 'Русский') {
        equal(
          (await dialog.locator('.inspector-subtitle').innerText()).trim(),
          'МАТРИАРХ',
          'panel name under the title',
        );
        ensure(body.includes('Боевой клич'), 'Russian panel text is second in ' + label);
      } else
        ensure(
          (await dialog.locator('.inspector-subtitle').innerText()).includes('M.O.T.H.E.R.'),
          'English subtitle',
        );
      equal(await param('card'), 'BE_036', 'card parameter lost');
      equal(
        await param('lang'),
        label === 'Русский' ? null : { Английский: 'en', Польский: 'pl', Испанский: 'es' }[label],
        'inspector lang param',
      );
      await page.screenshot({
        path:
          output +
          '/locale-inspector-' +
          { Русский: 'ru', Английский: 'en', Польский: 'pl', Испанский: 'es' }[label] +
          '.png',
      });
    }
    checks.inspectorFourLanguages = true;
    // keyboard: arrows on the focused option move the selection
    await inspectorSwitch.getByRole('radio', { name: 'Русский', exact: true }).focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'en');
    checks.keyboard = true;

    // ---- fallback and 404 in the inspector (fixture cards 2 and 3), localised render (6) ---------------
    // Closing the inspector (history.back) keeps the language chosen while it was open.
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'detached' });
    await page.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'en');
    checks.languageKeptOnClose = true;
    if (!live) {
      await load('card_type=constructed&view=grid&lang=pl');
      await gridName('BG_FIXTURE_2').click();
      await page
        .getByRole('dialog')
        .getByText('Нет перевода на польский — показан английский')
        .first()
        .waitFor();
      await page.screenshot({ path: output + '/locale-inspector-fallback.png' });
      await closeInspector();
      await gridName('BG_FIXTURE_3').click();
      await page
        .getByRole('dialog')
        .getByText('Нет в каталоге карт — показаны данные панели')
        .first()
        .waitFor();
      await closeInspector();
      await gridName('BG_FIXTURE_6').click();
      await page.waitForFunction(() =>
        document.querySelector('.inspector-art img')?.getAttribute('src')?.includes('test-render.webp'),
      );
      await closeInspector();
      await gridName('BG_FIXTURE_7').click();
      await page.getByRole('dialog').waitFor();
      await page.waitForFunction(
        () => document.querySelector('dialog h2')?.textContent === 'PL BG_FIXTURE_7',
      );
      ensure(
        !(await page.locator('.inspector-art img').first().getAttribute('src'))?.includes('test-render.webp'),
        'a card without render_url used the render',
      );
      checks.inspectorNotesAndRender = true;
    }

    // ---- cache: back to a visited language makes no requests ------------------------------------------
    await closeInspector();
    await load('card_type=constructed&view=grid&lang=pl');
    await page.waitForTimeout(700);
    const settledCount = requests.length;
    await choose('Русский', page.getByRole('radiogroup', { name: 'Язык карт' }).first());
    await choose('Польский', page.getByRole('radiogroup', { name: 'Язык карт' }).first());
    await page.waitForTimeout(300);
    equal(requests.length, settledCount, 'a repeated language made new requests');
    checks.cache = true;

    // ---- preference in localStorage ----------------------------------------------------------------------
    await choose('Испанский', page.getByRole('radiogroup', { name: 'Язык карт' }).first());
    const second = await page.context().newPage();
    await second.goto(root + '/?card_type=constructed');
    await second.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'es');
    await second.goto(root + '/?card_type=constructed&lang=en');
    await second.waitForTimeout(400);
    equal(
      await second.evaluate(() => localStorage.getItem('hs_card_lang')),
      'es',
      'a shared link changed the preference',
    );
    await second.goto(root + '/?card_type=constructed&lang=xx');
    await second.waitForFunction(() => !new URL(location.href).searchParams.has('lang'));
    // An unknown language with an open card (catalogue and inspector both read the address): Russian,
    // not the saved choice.
    await second.goto(root + '/?card_type=constructed&view=grid&card=BE_036&lang=xx');
    await second.getByRole('dialog').waitFor();
    await second.waitForFunction(() => !new URL(location.href).searchParams.has('lang'));
    await second.waitForTimeout(500);
    equal(
      await second.evaluate(() => new URL(location.href).searchParams.get('lang')),
      null,
      'unknown lang with a card brought the saved language back',
    );
    equal(
      await second.evaluate(() => new URL(location.href).searchParams.get('card')),
      'BE_036',
      'card lost',
    );
    equal(
      await second.locator('dialog h2').innerText(),
      'МАТРИАРХ',
      'unknown lang with a card is not Russian',
    );
    checks.unknownLangWithCard = true;
    // Unknown lang → a valid language → Back to the same address without lang: the saved choice returns
    // (the mark of the dropped address must not outlive the valid choice).
    await second.evaluate(() => localStorage.removeItem('hs_card_lang'));
    await second.goto(root + '/?card_type=constructed&view=grid');
    await second.locator('.card-tile').first().waitFor();
    await second.evaluate(() =>
      history.pushState(null, '', location.pathname + location.search + '&lang=xx'),
    );
    await second.waitForFunction(() => !new URL(location.href).searchParams.has('lang'));
    await choose('Польский', second.getByRole('radiogroup', { name: 'Язык карт' }).first());
    await second.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'pl');
    // Back only after the router committed the choice (the radio follows the address): a Back in the
    // same instant races Next's own history update, which no person can do.
    const polish = second.getByRole('radio', { name: 'Польский', exact: true }).first();
    for (let i = 0; i < 30 && !(await polish.isChecked()); i++) await second.waitForTimeout(100);
    await second.waitForTimeout(500);
    await second.goBack();
    await second.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'pl', null, {
      timeout: 3000,
    });
    checks.unknownThenValidBack = true;
    // Opening and closing a card of a link with ?lang=es does not rewrite the saved preference.
    await second.evaluate(() => localStorage.setItem('hs_card_lang', 'pl'));
    await second.goto(root + '/?card_type=constructed&view=grid&lang=es');
    await second
      .locator('.card-tile', { has: second.locator('code', { hasText: /^BE_036$/ }) })
      .locator('.card-name')
      .click();
    await second.getByRole('dialog').waitFor();
    await second.keyboard.press('Escape');
    await second.getByRole('dialog').waitFor({ state: 'detached' });
    await second.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'es');
    equal(
      await second.evaluate(() => localStorage.getItem('hs_card_lang')),
      'pl',
      'closing a card rewrote the saved language',
    );
    checks.closeKeepsPreference = true;
    await second.close();
    checks.preference = true;

    // ---- storage blocked: Russian, no errors --------------------------------------------------------------
    const blocked = await page.context().newPage();
    const blockedErrors = [];
    blocked.on('pageerror', error => blockedErrors.push(error.message));
    blocked.on(
      'console',
      message =>
        message.type() === 'error' &&
        !/Failed to load resource/.test(message.text()) &&
        blockedErrors.push(message.text()),
    );
    await blocked.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {
        get() {
          throw new Error('blocked');
        },
      });
    });
    await blocked.goto(root + '/?card_type=constructed&view=grid');
    await blocked.getByRole('radio', { name: 'Польский', exact: true }).waitFor();
    await blocked.getByRole('radio', { name: 'Польский', exact: true }).click();
    await blocked.waitForFunction(() => new URL(location.href).searchParams.get('lang') === 'pl');
    equal(blockedErrors.length, 0, 'errors without storage: ' + blockedErrors.join('; '));
    await blocked.close();
    checks.storageBlocked = true;

    // ---- 390 px ---------------------------------------------------------------------------------------------
    await page.setViewportSize({ width: 390, height: 844 });
    await load('card_type=constructed&view=grid&lang=pl&card=BE_036');
    await page.getByRole('dialog').waitFor();
    await page.waitForFunction(() => document.querySelector('dialog h2')?.textContent === 'M.A.T.K.A.');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    ensure(overflow <= 0, 'horizontal scroll at 390px: ' + overflow);
    await page.screenshot({ path: output + '/locale-inspector-pl-390.png' });
    checks.mobile = true;

    equal(pageErrors.length, 0, 'uncaught errors: ' + pageErrors.join('; '));
    ensure(
      consoleErrors.every(text => /Failed to load resource|net::ERR/.test(text)),
      'console errors: ' + consoleErrors.join('; '),
    );
    checks.noErrors = true;
    await page.evaluate(value => {
      window.localeQaResults = value;
    }, checks);
    return checks;
  } catch (error) {
    // Names the last passed check: a failure inside a run-code script has no stack of its own.
    throw Error('after ' + (Object.keys(checks).pop() || 'start') + ': ' + error.message);
  }
}

// Optional local browser gate; uses fixture sessions and blocks all remote requests.
if (process.argv[1] === new URL(import.meta.url).pathname) {
  const { spawnSync } = await import('node:child_process');
  const { mkdir, readFile } = await import('node:fs/promises');
  const origin = process.env.PANEL_TEST_ORIGIN || 'http://127.0.0.1:4182';
  if (!['127.0.0.1', 'localhost'].includes(new URL(origin).hostname))
    throw Error('Browser QA requires a local fixture origin.');
  const output = process.env.PANEL_QA_OUTPUT || '/tmp/koloda-card-locale-browser';
  await mkdir(output, { recursive: true });
  const live = process.env.PANEL_LOCALE_LIVE === '1';
  const fixtures = {};
  for (const locale of ['ruRU', 'enUS', 'plPL', 'esES'])
    fixtures[`be036-${locale}`] = JSON.parse(
      await readFile(new URL(`./fixtures/card-locale/be036-${locale}.json`, import.meta.url), 'utf8'),
    );
  const cli = process.env.PLAYWRIGHT_CLI || 'playwright-cli';
  const session = 'card-locale';
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
      await (${verify.toString()})(page,${JSON.stringify(origin)},${JSON.stringify(output)},${JSON.stringify(fixtures)},${live});
    }`;
    command('run-code', code);
    console.log(command('eval', '() => window.localeQaResults'));
  } finally {
    command('close');
  }
}
