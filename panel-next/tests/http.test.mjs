import test from 'node:test';
import assert from 'node:assert/strict';
const origin=process.env.PANEL_TEST_ORIGIN||'http://127.0.0.1:4180';
const cookie='koloda_admin=fixture-session';
test('anonymous visitors receive an auth redirect and API 401 without caching',async()=>{
 const page=await fetch(origin+'/',{redirect:'manual'});assert.equal(page.status,307);assert.match(page.headers.get('location'),/\/auth\/github/);
 const api=await fetch(origin+'/api/panel');assert.equal(api.status,401);assert.match(api.headers.get('cache-control'),/no-store/);
});
test('mutation relay rejects foreign origins and stale CSRF',async()=>{
 const form=new FormData();form.set('action','save');form.set('csrf','invalid');
 const foreign=await fetch(origin+'/api/panel',{method:'POST',headers:{Cookie:cookie,Origin:'https://evil.test'},body:form});assert.equal(foreign.status,403);
 const stale=await fetch(origin+'/api/panel',{method:'POST',headers:{Cookie:cookie,Origin:origin},body:form});assert.equal(stale.status,422);
});
test('GET token pages never repeat a previously issued secret',async()=>{
 const page=await fetch(origin+'/?action=api_tokens',{headers:{Cookie:cookie}});assert.equal(page.status,200);assert.match(page.headers.get('cache-control'),/no-store/);const html=await page.text();assert.doesNotMatch(html,/khs_v1_FixtureOnly_/);assert.match(html,/1\s234 запроса/u,'token usage must come from usage.request_count');
});
test('server registry and data remain behind the session boundary',async()=>{
 const registry=await fetch(origin+'/api/panel?action=analytics_registry',{headers:{Cookie:cookie}});assert.equal(registry.status,200);const payload=await registry.json();assert.equal(payload.modules.overview.title,'Все источники');
 assert.ok(Object.values(payload.modules).every(item=>!('path' in item)));
});
test('remembered tile view redirects to an explicit URL without bypassing authentication',async()=>{
 const anonymous=await fetch(origin+'/',{headers:{Cookie:'hs_catalog_view=tiles'},redirect:'manual'});
 assert.equal(anonymous.status,307);assert.match(anonymous.headers.get('location'),/\/auth\/github/);
 const saved=await fetch(origin+'/?q=murloc&page=2',{headers:{Cookie:cookie+'; hs_catalog_view=tiles'},redirect:'manual'});
 assert.ok([200,307].includes(saved.status));
 // Next emits a meta redirect when the loading boundary has already streamed.
 const html=await saved.text();
 const meta=html.match(/<meta[^>]*http-equiv="refresh"[^>]*content="[^"]*url=([^"]+)"/);
 const destination=saved.headers.get('location')||meta?.[1]?.replaceAll('&amp;','&');
 assert.ok(destination,'Remembered view must produce an HTTP or streamed redirect');
 const target=new URL(destination,origin);
 assert.equal(target.searchParams.get('view'),'tiles');assert.equal(target.searchParams.get('per_page'),'15');
 assert.equal(target.searchParams.get('q'),'murloc');assert.equal(target.searchParams.get('page'),'2');
 const explicit=await fetch(origin+'/?view=list',{headers:{Cookie:cookie+'; hs_catalog_view=tiles'},redirect:'manual'});
 assert.equal(explicit.status,200);
});
