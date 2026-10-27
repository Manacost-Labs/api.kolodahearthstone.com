// Operator-facing navigation, page states and form actions, asserted through roles and visible text.
export default async function verify(page, root, output) {
  const checks = {};
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const ensure = (condition, message) => {
    if (!condition) throw Error(message);
  };
  const sidebar = page.getByRole('navigation', { name: 'Разделы панели' });
  const crumbs = page.getByRole('navigation', { name: 'Расположение' });
  await page.setViewportSize({ width: 1440, height: 900 });

  await page.goto(root + '/?action=parsers');
  await page.getByRole('heading', { level: 1, name: 'Источники', exact: true }).waitFor();
  ensure(
    await sidebar
      .getByRole('list', { name: 'Мониторинг' })
      .getByRole('link', { name: 'Статистика' })
      .isVisible(),
    'Statistics is not in the visible Monitoring group',
  );
  ensure(
    !(await sidebar.getByRole('link', { name: 'Существа', exact: true }).isVisible()),
    'Catalogue categories stay unfolded outside the catalogue',
  );
  ensure((await crumbs.innerText()).replace(/\s+/g, ' ').trim() === 'Мониторинг Источники', 'Crumbs');
  checks.groupedNavigation = true;

  const hsguru = page.getByRole('row', { name: /^HSGuru/ });
  ensure(
    /просрочен на \d[\d\s]* (день|дня|дней)/u.test(await hsguru.innerText()),
    'An overdue next run is not flagged in the source row',
  );
  checks.overdueRun = true;

  await page.goto(root + '/?view=grid');
  await page.getByRole('heading', { level: 1, name: 'Карты Полей сражений' }).waitFor();
  ensure(
    await sidebar.getByRole('link', { name: 'Существа', exact: true }).isVisible(),
    'Catalogue categories are folded on the catalogue',
  );
  checks.catalogueUnfolds = true;

  await page.goto(root + '/?view=list&q=zzzz');
  await page.getByRole('heading', { name: 'Ничего не найдено' }).waitFor();
  await page.getByText('Нет записей по запросу', { exact: true }).waitFor();
  await page
    .getByText('По запросу «zzzz» записей нет. Проверьте написание или ищите по ID и DBF.', { exact: true })
    .waitFor();
  await page.getByRole('link', { name: 'Сбросить поиск и фильтры' }).click();
  await page
    .getByRole('button', { name: /^Мурлок-разведчик/ })
    .first()
    .waitFor();
  ensure(!new URL(page.url()).searchParams.has('q'), 'Reset kept the search');
  ensure(
    (await page.getByRole('searchbox', { name: 'Поиск карт' }).inputValue()) === '',
    'Reset kept the text',
  );
  checks.queryAwareEmptyState = true;

  await page.goto(root + '/?action=edit&id=2');
  await page.getByRole('heading', { level: 1, name: 'Редактировать карту' }).waitFor();
  await page.getByText('«Мурлок-полководец» · BG_FIXTURE_2', { exact: true }).waitFor();
  ensure(await page.getByRole('group', { name: 'Карта' }).isVisible(), 'Card field group is missing');
  ensure(
    await page.getByRole('group', { name: 'Характеристики и пул' }).isVisible(),
    'Stats group is missing',
  );
  ensure((await page.getByTitle('Обязательное поле').count()) === 2, 'Required fields are not marked');
  await page.setViewportSize({ width: 390, height: 640 });
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
  ensure(
    await page
      .getByRole('button', { name: 'Сохранить изменения' })
      .evaluate(button => button.getBoundingClientRect().bottom <= window.innerHeight),
    'Save is out of reach at the top of a long form',
  );
  await page.screenshot({ path: output + '/editor-mobile-sticky.png' });
  await page.setViewportSize({ width: 1440, height: 900 });
  const remove = page.getByRole('button', { name: 'Удалить карту' });
  await remove.click();
  await page.getByRole('dialog', { name: 'Удалить карту?' }).waitFor();
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Удалить карту?' }).waitFor({ state: 'hidden' });
  ensure(
    await remove.evaluate(button => document.activeElement === button),
    'Focus did not return to Delete',
  );
  await crumbs.getByRole('link', { name: 'Каталог' }).click();
  await page.getByRole('heading', { level: 1, name: 'Карты Полей сражений' }).waitFor();
  checks.editorActions = true;

  await page.goto(root + '/?action=new');
  await page.getByRole('heading', { level: 1, name: 'Новая карта' }).waitFor();
  ensure(await page.getByRole('button', { name: 'Создать карту' }).isVisible(), 'Create label is missing');
  ensure((await page.getByRole('button', { name: 'Удалить карту' }).count()) === 0, 'New card offers Delete');
  checks.newCardActions = true;

  await page.goto(root + '/?action=wiki_terms');
  await page.getByText('Словарь EN → RU для терминов Wiki: 1 термин.', { exact: true }).waitFor();
  await page.getByRole('searchbox', { name: 'Поиск термина' }).fill('zzz');
  await page.getByRole('heading', { name: 'Термины не найдены' }).waitFor();
  await page.getByText('По запросу «zzz» ничего нет.', { exact: true }).waitFor();
  await page.getByText('Показано 0 из 1; скрытые термины тоже сохранятся.', { exact: true }).waitFor();
  ensure(
    !(await page.getByRole('textbox', { name: 'Battlecry' }).isVisible()),
    'Filtered term stays visible',
  );
  const [save] = await Promise.all([
    page.waitForRequest(request => request.method() === 'POST' && request.url().endsWith('/api/panel')),
    page.getByRole('button', { name: 'Сохранить переводы' }).click(),
  ]);
  ensure(
    save.postData().includes('name="terms[mechanic][0][ru]"\r\n\r\nБоевой клич'),
    'A term hidden by the search was not submitted',
  );
  await page.getByRole('status').filter({ hasText: 'Изменения сохранены.' }).waitFor();
  checks.termsSearchAndSave = true;

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
  const output = process.env.PANEL_QA_OUTPUT || '/tmp/koloda-panel-ux-browser';
  await mkdir(output, { recursive: true });
  const cli = process.env.PLAYWRIGHT_CLI || 'playwright-cli';
  const session = 'ux-regression';
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
    console.log(command('eval', '() => window.uxQaResults'));
  } finally {
    command('close');
  }
}
