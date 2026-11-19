import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  archivedHistoryImage,
  dataVersions,
  historyChanges,
  historyIdentity,
  latestDataChange,
  type CardHistoryData,
} from '../lib/card-history.ts';

// Truncated answer of GET /api/v1/card-history/constructed_card/BE_036 (6 versions, newest first).
const be036: CardHistoryData = JSON.parse(
  readFileSync(new URL('./fixtures/card-history-be036.json', import.meta.url), 'utf8'),
);
const version = (versionId: number) => {
  const found = be036.versions.find(item => item.version_id === versionId);
  if (!found) throw new Error('fixture has no version ' + versionId);
  return found;
};
const mana = (versionId: number) => version(versionId).snapshot.mana_cost;

test('comparison preserves zero, distinguishes missing from null and ignores polling bookkeeping', () => {
  const changes = historyChanges(
    { attack: 0, health: 2, text_ru: 'Старый текст', updated_at: 'yesterday', source_payload: { a: 1 } },
    {
      attack: 1,
      health: 2,
      text_ru: 'Новый текст',
      mana_cost: null,
      updated_at: 'today',
      source_payload: { a: 2 },
    },
  );
  assert.deepEqual(
    changes.map(change => change.key),
    ['attack', 'mana_cost', 'text_ru'],
  );
  assert.equal(changes[0].before, 0);
  assert.equal(changes[1].before, undefined);
  assert.equal(changes[1].after, null);
  assert.deepEqual(
    historyChanges({ stats: { attack: 1, health: 2 } }, { stats: { health: 2, attack: 1 } }),
    [],
  );
});

test('historical preview requires archived bytes, matching digest and a local archive path', () => {
  const hash = 'a'.repeat(64);
  const version = {
    version_id: 1,
    observed_at: 'now',
    event: 'changed',
    snapshot: {},
    media: {
      card_image: {
        status: 'archived',
        archive_url: `/uploads/card-history-media/${hash}.png`,
        sha256: hash,
        captured_at: 'now',
      },
    },
  };
  assert.equal(
    archivedHistoryImage(version)?.url,
    `https://api.kolodahearthstone.com/uploads/card-history-media/${hash}.png`,
  );
  assert.equal(archivedHistoryImage({ ...version, media: {} }), null);
  assert.equal(
    archivedHistoryImage({
      ...version,
      media: { card_image: { ...version.media.card_image, status: 'unverified' } },
    }),
    null,
  );
  assert.equal(
    archivedHistoryImage({
      ...version,
      media: { card_image: { ...version.media.card_image, sha256: 'b'.repeat(64) } },
    }),
    null,
  );
  assert.equal(
    archivedHistoryImage({
      ...version,
      media: {
        card_image: { ...version.media.card_image, archive_url: 'https://untrusted.example/image.png' },
      },
    }),
    null,
  );
});
test('history identity isolates constructed, BG, golden and same-id library records', () => {
  assert.deepEqual(historyIdentity({ card_id: 'A' }, '/api/v1/constructed-cards'), {
    entity: 'constructed_card',
    cardId: 'A',
  });
  assert.deepEqual(historyIdentity({ card_id: 'A_G' }, '/api/v1/cards'), { entity: 'card', cardId: 'A_G' });
  assert.deepEqual(historyIdentity({ card_id: 'A' }, '/api/v1/libraries/reward'), {
    entity: 'library_card',
    cardId: 'reward:A',
  });
  assert.deepEqual(historyIdentity({ card_id: 'A', library: 'trinket' }, '/api/v1/libraries/trinket'), {
    entity: 'library_card',
    cardId: 'trinket:A',
  });
  assert.equal(historyIdentity({ card_id: 'A' }, '/other-service'), null);
  assert.equal(historyIdentity({}, '/api/v1/cards'), null);
});

test('BE_036 opens on the last real change: 29220 to 32380, mana 9 to 10', () => {
  assert.deepEqual(latestDataChange(be036.versions), { after: 32380, before: 29220 });
  assert.equal(mana(29220), 9);
  assert.equal(mana(32380), 10);
  const [older, newer] = [version(29220), version(32380)];
  assert.deepEqual(
    historyChanges(older.snapshot, newer.snapshot).map(change => [change.key, change.before, change.after]),
    [
      [
        'image_url',
        'https://d15f34w2p8l1cc.cloudfront.net/hearthstone/f1d0a999db274d35cbe0007314c676ce0712b33fa8332c3d1ae4aaeab0224958.png',
        'https://d15f34w2p8l1cc.cloudfront.net/hearthstone/9a947b36a7df4bb529a5e06e51ee2262d2f65663178fd88497aa15e7eb6bbfbc.png',
      ],
      ['mana_cost', 9, 10],
    ],
  );
});

test('the two newest BE_036 versions differ only in service fields', () => {
  const [newest, previous] = be036.versions;
  assert.deepEqual(historyChanges(previous.snapshot, newest.snapshot), []);
  assert.deepEqual(
    historyChanges(previous.snapshot, newest.snapshot, true).map(change => change.key),
    ['local_gold_image_url'],
  );
});

test('data filter hides service-only versions and keeps the baseline', () => {
  assert.deepEqual(
    dataVersions(be036.versions).map(version => version.version_id),
    [32380, 29220, 533],
  );
});

test('a card without a real change has no default comparison', () => {
  const noise = be036.versions.slice(0, 2);
  assert.equal(latestDataChange(noise), null);
  assert.equal(latestDataChange([]), null);
  assert.equal(latestDataChange(be036.versions.slice(0, 1)), null);
});

test('after-image of the 13.10 to 03.11 comparison is the archive of image_url, not the stale local copy', () => {
  const after = version(32380);
  // local_image_url of this version still points to the old 9-mana art
  const stale = after.media?.local_image_url.archive_url;
  assert.equal(
    stale,
    '/uploads/card-history-media/f1d0a999db274d35cbe0007314c676ce0712b33fa8332c3d1ae4aaeab0224958.png',
  );
  assert.equal(archivedHistoryImage(after)?.field, 'image_url');
  assert.equal(
    archivedHistoryImage(after)?.url,
    'https://api.kolodahearthstone.com' + after.media?.image_url.archive_url,
  );
  assert.notEqual(archivedHistoryImage(after)?.url, 'https://api.kolodahearthstone.com' + stale);
});

test('local copy is used only when no source image is archived', () => {
  const hash = 'c'.repeat(64);
  const version = {
    version_id: 1,
    observed_at: 'now',
    event: 'changed',
    snapshot: {},
    media: {
      local_image_url: {
        status: 'archived',
        archive_url: `/uploads/card-history-media/${hash}.png`,
        sha256: hash,
        captured_at: 'now',
      },
      image_url: { status: 'unverified', archive_url: null, captured_at: 'now' },
    },
  };
  assert.equal(archivedHistoryImage(version)?.field, 'local_image_url');
});
