import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
const output = process.env.KOLODA_PANEL_QA_OUTPUT || '/tmp/koloda-catalog-gallery-qa';
await fs.mkdir(output, {recursive: true});
const targets = await (await fetch('http://127.0.0.1:18766/json/list')).json();
const ws = new WebSocket(targets.find(t => t.type === 'page').webSocketDebuggerUrl);
await new Promise(resolve => ws.addEventListener('open', resolve, {once:true}));
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
  else if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') errors.push(message.params.args.map(a=>a.value||a.description).join(' '));
});
function call(method, params = {}) {
  return new Promise((resolve,reject) => { const next = ++id; pending.set(next,{resolve,reject}); ws.send(JSON.stringify({id:next,method,params})); });
}
async function evaluate(expression) {
  const result = await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
  if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
async function waitFor(expression) {
  for (let i=0;i<60;i++) {
    if (await evaluate(expression)) return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('State timeout: '+expression);
}
async function viewport(width,height) { await call('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:width<680}); }
async function navigate(url, ready) {
  await call('Page.navigate',{url});
  await waitFor('document.readyState === "complete"');
  if (ready) await waitFor(ready);
  await evaluate('document.fonts.ready.then(() => true)');
  await new Promise(resolve=>setTimeout(resolve,200));
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  await fs.writeFile(output+'/'+name,Buffer.from(result.data,'base64'));
}
async function click(selector) {
  const rect = await evaluate(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); e.scrollIntoView({block:'center'}); const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2}; })()`);
  await call('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...rect});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...rect});
  await new Promise(resolve=>setTimeout(resolve,150));
}

await call('Page.enable');
await call('Runtime.enable');
await call('Network.enable');
await call('Network.setBlockedURLs', {urls:['https://*']});
const url = 'http://127.0.0.1:18767/tests/catalog_panel_fixture.php';
const findings = {};
await viewport(1487,1058);
await navigate(url, 'document.querySelectorAll(".catalog-tile").length === 4');
await screenshot('desktop.png');
findings.desktop = await evaluate('({tiles:document.querySelectorAll(".catalog-tile").length,overflow:document.documentElement.scrollWidth > innerWidth,images:[...document.querySelectorAll(".catalog-tile-art img")].map(i=>({src:i.src,loaded:i.complete&&i.naturalWidth>0})),view:document.querySelector("[data-catalog-workbench]").dataset.catalogView})');
await click('.catalog-tile');
await waitFor('document.querySelector("[data-catalog-inspector]").open');
findings.dialog = await evaluate('({name:document.querySelector("[data-inspector-field=name]").textContent,focus:document.activeElement.hasAttribute("data-inspector-close"),factsVisible:!document.querySelector("[data-inspector-panel=details]").hidden})');
await screenshot('dialog.png');
await click('[data-inspector-tab=api]');
findings.api = await evaluate('[...document.querySelectorAll("[data-inspector-api-links] a")].map(a=>a.getAttribute("href"))');
await click('[data-inspector-tab=images]');
findings.images = await evaluate('document.querySelectorAll("[data-inspector-images] figure").length');
await click('[data-inspector-images] button');
findings.preview = await evaluate('document.querySelector("[data-inspector-preview]").open');
async function escape() {
 await call('Input.dispatchKeyEvent',{type:'keyDown',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
 await call('Input.dispatchKeyEvent',{type:'keyUp',key:'Escape',code:'Escape',windowsVirtualKeyCode:27,nativeVirtualKeyCode:27});
 await new Promise(resolve=>setTimeout(resolve,100));
}
await escape();
findings.previewClosed = await evaluate('!document.querySelector("[data-inspector-preview]").open && document.querySelector("[data-catalog-inspector]").open');
await escape();
findings.focusRestored = await evaluate('document.activeElement.classList.contains("catalog-tile") && !document.querySelector("[data-catalog-inspector]").open');
await click('[data-catalog-view=list]');
findings.list = await evaluate('!document.querySelector(".cards-table").hidden && document.querySelector("[data-catalog-gallery]").hidden');
await navigate(url);
findings.persisted = await evaluate('document.querySelector("[data-catalog-workbench]").dataset.catalogView');
await click('[data-catalog-view=grid]');
await click('[data-sidebar-toggle]');
findings.navigation = await evaluate('getComputedStyle(document.querySelector(".side-nav")).display !== "none"');
await escape();
await viewport(390,844);
await navigate(url);
await screenshot('mobile.png');
findings.mobile = await evaluate('({filtersVisible:[...document.querySelectorAll(".filter-controls select")].every(e=>e.getBoundingClientRect().height>0),overflow:document.documentElement.scrollWidth>innerWidth,columns:getComputedStyle(document.querySelector(".catalog-gallery")).gridTemplateColumns.split(" ").length})');
await click('.catalog-tile');
await screenshot('mobile-dialog.png');
findings.mobileDialog = await evaluate('({open:document.querySelector("[data-catalog-inspector]").open,overflow:document.querySelector("[data-catalog-inspector]").scrollWidth>document.querySelector("[data-catalog-inspector]").clientWidth})');
await escape();
await viewport(834,1112);
await navigate(url);
await screenshot('tablet.png');
findings.tablet = await evaluate('({overflow:document.documentElement.scrollWidth>innerWidth,columns:getComputedStyle(document.querySelector(".catalog-gallery")).gridTemplateColumns.split(" ").length})');
await navigate(url+'?empty=1');
findings.empty = await evaluate('!!document.querySelector(".catalog-empty") && !document.querySelector(".catalog-tile")');
findings.errors = errors;
assert.equal(findings.desktop.tiles, 4);
assert.equal(findings.desktop.view, 'grid');
assert.equal(findings.desktop.overflow, false);
assert.equal(findings.dialog.name, 'Мурлок-разведчик');
assert.equal(findings.dialog.focus, true);
assert.equal(findings.dialog.factsVisible, true);
assert.deepEqual(findings.api, ['/api/v1/cards/BG26_146','/api/v1/cards/by-dbf/98582','/api/v1/cards/BG26_146_G','/api/v1/cards/by-dbf/98585']);
assert.equal(findings.images, 8);
assert.equal(findings.preview, true);
assert.equal(findings.previewClosed, true);
assert.equal(findings.focusRestored, true);
assert.equal(findings.list, true);
assert.equal(findings.persisted, 'list');
assert.equal(findings.navigation, true);
assert.equal(findings.mobile.filtersVisible, true);
assert.equal(findings.mobile.overflow, false);
assert.equal(findings.mobile.columns, 2);
assert.equal(findings.mobileDialog.open, true);
assert.equal(findings.mobileDialog.overflow, false);
assert.equal(findings.tablet.overflow, false);
assert.equal(findings.tablet.columns, 3);
assert.equal(findings.empty, true);
assert.deepEqual(findings.errors, []);
await fs.writeFile(output+'/checks.json', JSON.stringify(findings,null,2));
console.log(JSON.stringify(findings,null,2));
ws.close();
