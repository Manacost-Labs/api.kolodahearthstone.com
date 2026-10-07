import test from 'node:test';
import assert from 'node:assert/strict';
import { backendOrigin, bridgeTarget, sessionCookie, sameOrigin } from '../lib/transport.ts';
test('backend never accepts URL credentials, redirects or non-HTTPS remote origins', () => {
  assert.equal(backendOrigin('http://127.0.0.1:18768'), 'http://127.0.0.1:18768');
  for (const value of ['http://example.com','https://user:pass@example.com','https://example.com/path','file:///tmp/data']) assert.throws(()=>backendOrigin(value));
  assert.throws(()=>bridgeTarget('https://example.com',new URLSearchParams()));
  assert.throws(()=>bridgeTarget('panel',new URLSearchParams({action:'export_credentials'})));
});
test('only the named PHP session reaches the authenticated backend', () => {
  assert.equal(sessionCookie('unrelated=secret; koloda_admin=abc-123; tracker=secret'),'koloda_admin=abc-123');
  assert.equal(sessionCookie('koloda_admin=bad\r\nAuthorization: secret'),'');
  assert.equal(sessionCookie('tracker=secret'),'');
});
test('mutations require the actual same origin, including scheme and port', () => {
  assert.equal(sameOrigin('https://api.kolodahearthstone.com','https://api.kolodahearthstone.com/api/panel'),true);
  for (const origin of [null,'https://evil.test','http://api.kolodahearthstone.com','https://api.kolodahearthstone.com:123']) assert.equal(sameOrigin(origin,'https://api.kolodahearthstone.com/api/panel'),false);
});
