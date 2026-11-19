# Card changes feed (patch window)

Public, read-only. Answers "which cards really changed between two moments"
(for example a patch release) without knowing the card ids in advance. It is
built on the observed versions described in [card-version-media.md](card-version-media.md);
times are observation times in UTC, never a guessed release date, and history
before the first observation (2026-10-11) is unknown.

## `GET /api/v1/card-history/changes`

| Parameter | Meaning |
| --- | --- |
| `from` | Required. Window start, UTC, inclusive. `2026-11-03`, `2026-11-03 15:55`, `2026-11-03T15:55:00Z` or with an offset (`+03:00`). |
| `to` | Window end, UTC, exclusive. Default: now. |
| `entity` | `constructed_card` (Standard/Wild; image key `image_url`) or `card` (Battlegrounds; image key `card_image`); comma list allowed. Default: both. |
| `page`, `per_page` | Default 1 / 50, at most 200 per page. Cards are ordered by entity, then `card_id`. |

The window may not be longer than 14 days; `to` must be later than `from`.
Bad or missing dates, an unknown `entity` and an over-long window return `400`
with `error.code = invalid_parameter` and a Russian explanation. A window with
no changes returns an empty `changes` list.

```json
{"data": {
  "window": {"from": "2026-11-03T15:55:00Z", "to": "2026-11-05T00:00:00Z", "to_exclusive": true},
  "entities": ["constructed_card", "card"],
  "changes": [{
    "entity": "constructed_card", "card_id": "BE_036", "name_ru": "МАТРИАРХ", "name_en": "M.O.T.H.E.R.",
    "group": {"class_slug": "neutral", "card_type": "minion", "rarity": "legendary", "collectible": true, "card_set": "be"},
    "changed_fields": ["mana_cost"],
    "fields": {"mana_cost": {"before": 9, "after": 10}},
    "before": {"version_id": 29220, "observed_at": "2026-10-13 20:29:02.769992", "event": "changed",
      "image": {"field": "image_url", "status": "archived", "url": "/uploads/card-history-media/f1d0….png",
                "sha256": "f1d0…", "mime_type": "image/png", "evidence": "captured_after_observation",
                "captured_at": "2026-10-13 22:32:13.569662"}},
    "after": {"version_id": 32380, "observed_at": "2026-11-03 20:29:37.596806", "event": "changed",
      "image": {"field": "image_url", "status": "archived", "url": "/uploads/card-history-media/9a94….png", "…": "…"}}
  }],
  "coverage": {"before_first_observation": "unknown", "timestamps": "observed_at", "versions_scanned": 16,
               "last_observed_at": "2026-11-04 09:22:41.301397", "battlegrounds_text": "from_snapshot_notes"},
  "pagination": {"page": 1, "per_page": 50, "total": 7, "total_pages": 1}}}
```

* **before** is the last stored version observed before `from`. A card first
  stored inside the window has no earlier version, except that the baseline row
  the history writes just before its first change is used as `before`; a card
  that merely appeared in the window is not a change and is not listed.
* **after** is the last version inside the window whose visible fields differ
  from the version before it. Versions observed after `to` are never used. A
  card whose fields end the window as they began it (changed and reverted) is
  not listed.
* `image.url` is set only when `status` is `archived` (a content-addressed
  `/uploads/card-history-media/{sha256}.{ext}` path on the API host);
  otherwise `url` is `null` and `status` says why (`unverified`, `missing`, …).
  `evidence: captured_after_observation` means the bytes were captured after the
  version was observed.
* `changed_fields` / `fields` list only the player-visible fields: constructed
  `name_ru, name_en, mana_cost, attack, health, durability, armor, text_ru,
  text_en, card_type, rarity, class_slug, minion_type, spell_school,
  collectible, card_set`; Battlegrounds `name, name_en, tavern_tier, attack,
  health, text_ru, text_en, card_type, creature_type, in_pool, duos_only`.
  Battlegrounds text is taken from the snapshot `notes` column (the ability text
  before the `Механики:` line); the older `legacy` journal is not needed.
* **Ignored** (never a change): `local_image_url`, `local_gold_image_url`,
  `source_hash`, `source_payload_json`, `updated_at`, `last_seen_at`,
  `changed_at`, wiki and image URL columns, golden Battlegrounds variants,
  and formatting noise: stray `&`, line wraps, non-breaking spaces, a leading
  `[x]`, upper/lower case of names and of enum-like fields. A field that is
  empty on either side is lost data, not a change.
* Image-only changes (new art with identical numbers and text) are not reported
  yet: the rendered image URL changes on every re-render.

Cache: `Cache-Control: public, max-age=600` for a window that ended more than
10 minutes ago, `120` otherwise; `ETag` is the response hash and `304` is
supported. A `503 history_not_ready` means the history tables are not installed.
A window that holds more than 60 000 versions is refused with
`400 window_too_large`; narrow `from`/`to`.

### How the window is found

Version ids grow with `observed_at`. The window's first and last id are found by
binary search over primary-key probes (about 17 each), widened by 300 ids for
concurrent inserts, and the rows are read with `id > :after AND id <= :high`
plus the exact time filter, in chunks of 400. There is no scan by
`observed_at`, and snapshots are reduced to a few normalized fields as they are
read. The "before" versions come from one query per 200 cards through
`history_lookup (entity, card_id, id)`; media for the returned page only come
from the media primary key.

## `GET /api/v1/card-history/waves`

A hint for "which patch was it": hours in which at least `min_cards` (default 5,
1–1000) different cards really changed, merged into a wave when they are less
than 18 hours apart.

| Parameter | Meaning |
| --- | --- |
| `since` | Required, UTC, inclusive. |
| `until` | UTC, exclusive. Default: now. Period at most 31 days. |
| `min_cards` | Cards per hour that make an hour busy. |

```json
{"data": {"since": "2026-10-20T00:00:00Z", "until": "2026-11-19T17:15:00Z", "min_cards_per_hour": 3,
  "waves": [{"from": "2026-11-03T20:00:00Z", "to": "2026-11-04T09:00:00Z",
             "first_change_at": "2026-11-03 20:29:37.541686", "last_change_at": "2026-11-04 08:13:07.991560",
             "cards": 7, "by_entity": {"constructed_card": 3, "card": 4},
             "peak_hour": {"hour": "2026-11-04T08:00:00Z", "cards": 4}}],
  "coverage": {"before_first_observation": "unknown", "timestamps": "observed_at", "versions_scanned": 27,
               "last_observed_at": "2026-11-18 10:39:29.483801"}}}
```

`from`/`to` of a wave are whole UTC hours and can be passed to `changes`
(widen them if a few cards changed outside the busy hours). A wave is a hint,
not a patch date. Cached for 10 minutes.
