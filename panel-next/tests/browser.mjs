import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const output = process.env.PANEL_QA_OUTPUT || '/tmp/koloda-next-browser-qa';
await fs.mkdir(output, { recursive: true });
const targets = await (await fetch('http://127.0.0.1:18766/json/list')).json();
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve, { once: true }));
let id = 0;
const pending = new Map();
const errors = [];
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const callbacks = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) callbacks.reject(new Error(message.error.message));
    else callbacks.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') errors.push(message.params.exceptionDetails.text);
  else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error')
    errors.push(message.params.args.map(a => a.value || a.description).join(' '));
});
function call(method, params = {}) {
  return new Promise((resolve, reject) => {
    const next = ++id;
    pending.set(next, { resolve, reject });
    ws.send(JSON.stringify({ id: next, method, params }));
  });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function waitFor(expression) {
  for (let i = 0; i < 60; i++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('State timeout: ' + expression);
}
async function viewport(width, height) {
  await call('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor: 1,
    mobile: width < 680,
  });
}
async function navigate(url, ready) {
  await call('Page.navigate', { url });
  await waitFor('document.readyState === "complete"');
  if (ready) await waitFor(ready);
  await evaluate('document.fonts.ready.then(() => true)');
  await new Promise(resolve => setTimeout(resolve, 200));
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await fs.writeFile(output + '/' + name, Buffer.from(result.data, 'base64'));
}
async function click(selector) {
  const rect = await evaluate(
    `(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`,
  );
  await call('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...rect });
  await call('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...rect });
  await new Promise(resolve => setTimeout(resolve, 150));
  await waitFor('!document.querySelector("dialog[data-closing]")');
}

await call('Page.enable');
await call('Runtime.enable');
await call('Network.enable');
await call('Network.setBlockedURLs', { urls: ['https://*'] });
await call('Network.setCookie', {
  name: 'koloda_admin',
  value: 'fixture-session',
  url: 'http://127.0.0.1:4180',
  httpOnly: true,
});
const root = 'http://127.0.0.1:4180/';
const checks = {};
await viewport(1487, 1058);
await navigate(root, 'document.querySelectorAll(".card-tile").length===8');
await screenshot('catalog-desktop.png');
checks.catalog = await evaluate(
  '({cards:document.querySelectorAll(".card-tile").length,overflow:document.documentElement.scrollWidth>innerWidth})',
);
await click('.card-tile');
await waitFor('!!document.querySelector("dialog[open]")');
checks.details = await evaluate(
  '({name:document.querySelector("dialog h2").textContent,focus:document.activeElement.getAttribute("aria-label")})',
);
checks.cardUrl = await evaluate('new URLSearchParams(location.search).get("card")');
assert.equal(checks.cardUrl, 'BG_FIXTURE_1');
await click('.inspector-more summary');
await click('#detail-tab-json');
checks.api = await evaluate('[...document.querySelectorAll(".api-links a")].map(a=>a.getAttribute("href"))');
assert.equal(
  await evaluate('document.querySelector("#detail-tab-json").getAttribute("aria-selected")'),
  'true',
);
assert.ok(checks.api.includes('/api/v1/cards/BG_FIXTURE_1_G'));
assert.ok(checks.api.includes('/api/v1/cards/by-dbf/79000'));
// Flipping cards keeps one dialog open (no close/reopen flash) and keeps focus on the control.
await evaluate('document.querySelector("dialog[open]").dataset.qa = "inspector"');
await click('.inspector-nav button[aria-label="Следующая запись"]');
await waitFor('new URLSearchParams(location.search).get("card")==="BG_FIXTURE_2"');
await call('Input.dispatchKeyEvent', {
  type: 'keyDown',
  key: 'ArrowRight',
  code: 'ArrowRight',
  windowsVirtualKeyCode: 39,
});
await call('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'ArrowRight',
  code: 'ArrowRight',
  windowsVirtualKeyCode: 39,
});
await waitFor('new URLSearchParams(location.search).get("card")==="BG_FIXTURE_3"');
checks.flip = await evaluate(
  '({same:document.querySelector("dialog[open]").dataset.qa==="inspector",position:document.querySelector(".inspector-nav span").textContent,focus:document.activeElement.getAttribute("aria-label")})',
);
assert.deepEqual(checks.flip, { same: true, position: '3 из 8', focus: 'Следующая запись' });
async function pressEscape() {
  await call('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Escape',
    code: 'Escape',
    windowsVirtualKeyCode: 27,
    nativeVirtualKeyCode: 27,
  });
  await call('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Escape',
    code: 'Escape',
    windowsVirtualKeyCode: 27,
    nativeVirtualKeyCode: 27,
  });
  await waitFor('!document.querySelector("dialog[data-closing]")');
}
await pressEscape();
await waitFor('!new URLSearchParams(location.search).has("card")');
checks.focusRestored = await evaluate('document.activeElement.classList.contains("card-tile")');
// A shared ?card= link opens the record even when it is not on the current page.
await navigate(root + '?card=BG_FIXTURE_20', '!!document.querySelector("dialog[open] .inspector")');
checks.deepLink = await evaluate('document.querySelector("dialog h2").textContent.length>0');
await pressEscape();
await waitFor('!document.querySelector("dialog[open]") && !new URLSearchParams(location.search).has("card")');
await navigate(root, 'document.querySelectorAll(".card-tile").length===8');
await call('Input.dispatchKeyEvent', {
  type: 'keyDown',
  key: '/',
  code: 'Slash',
  windowsVirtualKeyCode: 191,
});
await call('Input.dispatchKeyEvent', { type: 'keyUp', key: '/', code: 'Slash', windowsVirtualKeyCode: 191 });
checks.searchShortcut = await evaluate('document.activeElement.name==="q"');
assert.equal(checks.searchShortcut, true);
await call('Input.dispatchKeyEvent', {
  type: 'keyDown',
  key: 'k',
  code: 'KeyK',
  modifiers: 2,
  windowsVirtualKeyCode: 75,
});
await call('Input.dispatchKeyEvent', {
  type: 'keyUp',
  key: 'k',
  code: 'KeyK',
  modifiers: 2,
  windowsVirtualKeyCode: 75,
});
await waitFor('!!document.querySelector("dialog[open] .quick-links")');
checks.quickNavigation = true;
await pressEscape();
await click('.pagination button:nth-child(3)');
await waitFor(
  'location.search.includes("page=2") && document.querySelector(".card-copy code").textContent==="BG_FIXTURE_9"',
);
checks.pagination = true;
await navigate(root);
await click('.search input');
await call('Input.insertText', { text: 'BG_FIXTURE_24' });
await waitFor('document.querySelectorAll(".card-tile").length===1');
checks.search = true;
await navigate(root + '?q=NOT_FOUND');
checks.empty = await evaluate('!!document.querySelector(".empty")&&!document.querySelector(".card-tile")');
await navigate(root);
await click('.view-switch button:nth-child(3)');
await waitFor('document.querySelectorAll("tbody tr").length===8');
checks.list = await evaluate('document.querySelectorAll("tbody tr").length===8');
await navigate(root);
checks.savedView = await evaluate('document.querySelectorAll("tbody tr").length===8');
await click('.view-switch button:nth-child(2)');
await waitFor('document.querySelectorAll(".card-tile").length===8');
await viewport(390, 844);
await navigate(root);
await screenshot('catalog-mobile.png');
checks.mobile = await evaluate(
  '({overflow:document.documentElement.scrollWidth>innerWidth,columns:getComputedStyle(document.querySelector(".card-grid")).gridTemplateColumns.split(" ").length})',
);
await click('.card-tile');
await screenshot('detail-mobile.png');
checks.mobileDialog = await evaluate(
  'document.querySelector("dialog").scrollWidth<=document.querySelector("dialog").clientWidth',
);
await pressEscape();
await viewport(1487, 1058);
await navigate(root + '?action=analytics', '!!document.querySelector("tbody tr")');
await screenshot('analytics-desktop.png');
await click('tbody .link-button');
checks.analyticsDetails = await evaluate('!!document.querySelector("dialog[open]")');
await pressEscape();
await navigate(root + '?action=parsers', 'document.querySelectorAll("tbody tr").length===3');
await screenshot('parsers-desktop.png');
await click('tbody tr:first-child .row-actions button:last-child');
checks.runConfirm = await evaluate('!!document.querySelector("dialog[open]")');
await click('dialog .form-actions button:last-child');
await waitFor('!document.querySelector("dialog[open]")');
await click('.section-tabs button:nth-child(2)');
await click('.form-actions button:nth-child(2)');
await click('dialog .form-actions button:last-child');
await waitFor('!document.querySelector("dialog[open]")');
checks.sectionRevision = true;
await navigate(root + '?action=api_tokens', '!!document.querySelector(".token-layout")');
await screenshot('tokens-desktop.png');
await click('input[name=name]');
await call('Input.insertText', { text: 'Fixture rejected' });
await click('.panel-card button[type=submit]');
await waitFor('!!document.querySelector(".notice.bad")');
await evaluate('document.querySelector("input[name=name]").value=""');
checks.failedIssueRecovery = true;
await click('input[name=name]');
await call('Input.insertText', { text: 'Fixture client' });
await click('.panel-card button[type=submit]');
await waitFor('!!document.querySelector(".secret-card")');
checks.issue = true;
await click('.panel-card button[type=submit]');
await waitFor('document.querySelectorAll("tbody tr").length===4');
checks.freshNonce = true;
await click('tbody tr:first-child .link-button');
await click('dialog .form-actions button:last-child');
await waitFor('!document.querySelector("dialog[open]")');
checks.revoke = true;
await navigate(root + '?action=edit&id=1', '!!document.querySelector(".editor-form")');
await screenshot('editor-desktop.png');
await click('.editor-form button[type=submit]');
// Back in the catalogue; a saved hs_catalog_view cookie may add ?view=… to "/".
await waitFor('location.pathname==="/" && !new URLSearchParams(location.search).has("action")');
checks.save = true;
await navigate(root + '?action=wiki_terms');
checks.terms = await evaluate('!!document.querySelector("input[name*=terms]")');
for (const action of ['analytics', 'parsers', 'api_tokens']) {
  await viewport(390, 844);
  await navigate(root + '?action=' + action);
  checks[action + 'MobileOverflow'] = await evaluate('document.documentElement.scrollWidth>innerWidth');
  await screenshot(action + '-mobile.png');
}
checks.errors = errors;
assert.deepEqual(checks.catalog, { cards: 8, overflow: false });
assert.equal(checks.details.focus, 'Закрыть');
assert.equal(checks.focusRestored, true);
assert.equal(checks.deepLink, true);
assert.equal(checks.pagination, true);
assert.equal(checks.search, true);
assert.equal(checks.empty, true);
assert.equal(checks.list, true);
assert.equal(checks.savedView, true);
assert.deepEqual(checks.mobile, { overflow: false, columns: 2 });
assert.equal(checks.mobileDialog, true);
assert.equal(checks.analyticsDetails, true);
assert.equal(checks.runConfirm, true);
assert.equal(checks.sectionRevision, true);
assert.equal(checks.issue, true);
assert.equal(checks.freshNonce, true);
assert.equal(checks.revoke, true);
assert.equal(checks.save, true);
assert.equal(checks.terms, true);
for (const a of ['analytics', 'parsers', 'api_tokens']) assert.equal(checks[a + 'MobileOverflow'], false);
assert.deepEqual(errors, []);
await fs.writeFile(output + '/checks.json', JSON.stringify(checks, null, 2));
console.log(JSON.stringify(checks, null, 2));
ws.close();
