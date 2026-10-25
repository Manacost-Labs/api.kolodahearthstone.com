# Unified GraphQL API

The public read-only endpoint is:

```text
POST https://api.kolodahearthstone.com/v1/graphql
Content-Type: application/json
```

It reads the central PostgreSQL `hub` and `catalog` schemas directly. It does
not call `db.kolodahs.ru` or `api.hs-manacost.ru`, so those domains can be
retired after every client has moved.

During cutover, the new host also serves the existing REST paths (for example
`/v1/battlegrounds/heroes` and `/v1/sources`). Existing clients can therefore move
to the new hostname first and adopt GraphQL separately.

`POST /v1/` remains a deprecated compatibility alias during migration.

## Query roots

| Field | Data |
|---|---|
| `health` | PostgreSQL availability and source freshness |
| `cards` / `card` | Constructed and Battlegrounds card catalogue |
| `battlegroundHeroes` | Heroes, hero art, powers, buddies and availability |
| `statistics` | Normalized statistics for cards, heroes, modes and other entities |
| `statisticHistory` | Every stored snapshot for one entity, optionally filtered by patch |
| `compareStatisticPatches` | Before/after values and deltas for two patches |
| `archetypes` | Latest archetype data split by rank, format and region |
| `battlegroundMinions` | Minion performance split by tier, MMR and time range |
| `battlegroundMinionStats` | Full HSReplay statistics of one minion (`minion`: dbf or card id; `mmrPercentile`, `timeRange`, `compositionId`): normal/golden totals, per-round arrays, board positions, copy purchase rates and medians; JSON with the same shape as REST `/v1/battlegrounds/minions/{minion}/stats` |
| `hsguruMatchups` | HSGuru archetype-versus-archetype matrix of one slice: `format`, `rank`, `period` (default: the Bans window), `coin` (`any_player` only), `minGames`, `minArchetypeGames`, `limit`. Read from the API's own SQLite store, not the PostgreSQL mirror. JSON with the same shape as REST `/v1/hsguru/matchups` `data` |
| `hsguruMulligan` | HSGuru mulligan guide of an archetype (`archetype` + `format`) or deck (`deckId`) for `rank`, `period` (`current` allowed), `coin`, `opponentClass`; optional `minMullCount`/`minDrawnCount`. JSON with the same shape as REST `/v1/hsguru/mulligan` `data` |
| `hsguruClassMatchups` | HSGuru winrate of an archetype or deck against each class with the Total row; same shape as REST `/v1/hsguru/class-matchups` `data` |
| `hsguruBrawlMeta` | Current Tavern Brawl archetype meta (`rank`, `coin`, `opponentClass`, `minGames`); same shape as REST `/v1/hsguru/brawl/meta` `data` |
| `hsguruBrawlDecks` | Current Tavern Brawl decks (`rank`, `className`, `archetype`); same shape as REST `/v1/hsguru/brawl/decks` `data` |
| `hsguruEsportsTournaments` | Tournament summaries (`kind`), one tournament with standings and lineups (`tournamentId`) or `/esports` upcoming tournaments (`upcoming: true`) |
| `hsguruEsportsPoints` | HSEsports points of a season (`season`, `leaderboard` STD/BG_LL, `region`); same shape as REST `/v1/hsguru/esports/points` `data` |
| `hsguruEsportsLegacy` | Archived Masters Tour table (`table`: mt-stats, qualifier-stats, mt-points; `selector`) |
| `hsreplayMulligan` | HSReplay mulligan v2 of one deck slice: `deck` (HSReplay shortid), `format`, `rank` (`legend`, `diamond_4to1`, `diamond`, `bronze_gold`), `initiative` (`all`, `first`, `coin`), `period` (`last_30_days`, `current_patch`, `current_expansion`), `opponentClass`. JSON with the same shape as REST `/v1/hsreplay/mulligan` `data` (`source: "hsreplay"`); `null` when not collected; error `PREMIUM_SESSION_UNAVAILABLE` (with `extensions.premiumSession`) when a premium slice cannot be served because the HSReplay session is rejected |
| `hsreplayCardChoices` | What players pick from one choice card (Discover, Adapt, Dredge) on HSReplay plus the card's full option pool: `card` (card id or dbf id), `format` (`standard`/`wild`, default Standard when legal), `rank` (`legend`, `bronze_gold`; `diamond_4to1`, `diamond` when enabled). JSON with the same shape as REST `/v1/hsreplay/card-choices` `data` (`options[]` with `popularity`, `win_rate`, `low_sample`, `undervalued`; `pool` with `resolved`, `cards[].has_stats`, Excavate `tiers`); `null` for an unknown card or a card without a choice |
| `hsguruStreamers` | HSGuru streamers (`q`, `format`, `playerClass`, `language`, `live`, `days`, `sort`, `limit`, `offset`, `decks`): links, live stream, last decks; JSON with the same shape as REST `/v1/hsguru/streamers` (`items`, `total`) |
| `hsguruStreamer` / `hsguruStreamerDecks` | One streamer (`streamer`: login, name or Twitch id) and their decks (`format`, `playerClass`, `days`, `limit`, `offset`); same shapes as REST `/v1/hsguru/streamers/{streamer}` and `/decks` |
| `hsguruStreamingNow` | Live streams with the current deck and mode (`language`, `mode`, `known`, `limit`); the mirror trails the five-minute refresh by up to 15 minutes |
| `sources` | Integrated sources and their last update state |
| `search` | One search across cards, minions, heroes, archetypes and sources |
| `datasets` | Latest imported version and payload size for each source |
| `dataset` | One complete raw dataset for compatibility during migration |
| `collections` | Every public table/view and its columns; requires `database:read` |
| `records` | Paginated rows from any public collection; requires `database:read` |

Legacy application tables copied with the MariaDB catalogue (`catalog.jobs`,
`catalog.options`, `catalog.migration`) hold user jobs and settings, not card
data. `collections` does not list them, and `records` answers
`collection does not exist` for them.

Every collection returns `items` and `pageInfo`. `limit` defaults to 50 or 100
and cannot exceed 200. `offset` cannot exceed 100,000.

Every large collection supports keyset pagination. This includes `cards`,
`battlegroundHeroes`, `statistics`, `statisticHistory`, `archetypes`,
`battlegroundMinions`, `sources`, `search`, `datasets`, `collections` and
`records`. Read `pageInfo.nextCursor` and pass it as `after` in the next query.
Do not combine `after` with a non-zero `offset`; keep all filters unchanged
between pages. The cursor is opaque and must be returned without editing it.

```graphql
query NextCardsPage($after: String) {
  cards(limit: 50, after: $after) {
    items { cardId nameRu imageUrl horizontalImageUrl }
    pageInfo { hasNextPage nextCursor }
  }
}
```

`horizontalImageUrl` is a deterministic `320×64` WebP crop intended for compact
rows and horizontal card containers. It is separate from the original artwork and
can be `null` when an object has no suitable source art.

The REST database endpoints expose the same asset as `images.horizontal`, for
example in `/api/v1/cards`, `/api/v1/constructed-cards`, `/api/v1/heroes`,
`/api/v1/hero-skins`, `/api/v1/pets`, `/api/v1/coins`,
`/api/v1/timewarped-cards` and every `/api/v1/libraries/*` collection.

Unified search:

```graphql
query SearchEverything($after: String) {
  search(query: "Reno", kinds: [CARD, HERO, ARCHETYPE], after: $after, limit: 30) {
    items { kind entityId name nameRu subtitle imageUrl sourceId metadata }
    pageInfo { total hasNextPage nextCursor }
  }
}
```

Patch comparison:

```graphql
query PatchDelta {
  compareStatisticPatches(
    entityKey: "wild:reno-priest"
    fromPatch: "34.0"
    toPatch: "34.1"
    formatName: "wild"
  ) {
    before { patch games winRate popularity }
    after { patch games winRate popularity }
    deltas { metric beforeValue afterValue absoluteChange percentChange }
  }
}
```

## HSGuru Brawl and esports

The five fields return JSON with the same shape as the `data` of the REST
endpoints (`/v1/hsguru/brawl/*`, `/v1/hsguru/esports/*`, see
[API.md](API.md#hsguru-brawl-and-esports)). They read the latest dataset
imported into `raw.datasets`, so a new snapshot appears after the next
import. An unknown tournament, season or table returns `null`; an invalid
selector returns `VALIDATION_ERROR`.

| Field | Arguments (defaults) |
|---|---|
| `hsguruBrawlMeta` | `rank` (`all`), `coin` (`any_player`), `opponentClass` (`any`), `minGames` (`10`) |
| `hsguruBrawlDecks` | `rank` (`all`), `className`, `archetype` |
| `hsguruEsportsTournaments` | `tournamentId`; `kind` (`tour_stop`/`community`); `upcoming` (`false`) |
| `hsguruEsportsPoints` | `season` (latest whole year), `leaderboard` (`STD`/`BG_LL`), `region` (`EU`/`AM`/`AP`/`CN`) |
| `hsguruEsportsLegacy` | `table` (`mt-stats`, `qualifier-stats`, `mt-points`), `selector` |

```graphql
query HsguruBrawlAndEsports {
  brawl: hsguruBrawlMeta(rank: "all", opponentClass: "MAGE", minGames: 50)
  brawlDecks: hsguruBrawlDecks(className: "Priest")
  upcoming: hsguruEsportsTournaments(upcoming: true)
  tourStops: hsguruEsportsTournaments(kind: "tour_stop")
  summer: hsguruEsportsTournaments(tournamentId: "6a6c88f821e3c30013db3d46")
  points: hsguruEsportsPoints(season: "2026", leaderboard: "STD", region: "EU")
  qualifiers: hsguruEsportsLegacy(table: "qualifier-stats", selector: "2021")
}
```

Excerpt of the response:

```json
{
  "data": {
    "brawl": {
      "rank": "all", "coin": "any_player", "opponent_class": "MAGE", "min_games": 50,
      "items": [{"archetype": "C'Thun Priest", "winrate": 71.3, "popularity": 21.1, "games": 338}]
    },
    "points": {
      "season": "2026", "leaderboard": "STD",
      "items": [{"rank": 1, "player": {"name": "Gaby59", "region": "EU", "country": "FR"}, "total": 40}]
    }
  }
}
```

## Example

```graphql
query DatabaseOverview {
  health {
    status
    latestSyncAt
  }

  cards(collection: "constructed", search: "Reno", limit: 20) {
    items {
      cardId
      nameRu
      nameEn
      manaCost
      imageUrl
    }
    pageInfo {
      total
      hasNextPage
    }
  }

  statistics(
    domain: "constructed"
    formatName: "wild"
    rankRange: "legend"
    limit: 20
  ) {
    items {
      sourceId
      entityType
      name
      games
      winRate
      popularity
      fetchedAt
    }
    pageInfo {
      total
    }
  }
}
```

HTTP request body:

```json
{
  "query": "query { health { status databaseConnected sourceCount latestSyncAt } }"
}
```

## Error contract

Expected application errors use `errors[].extensions.code`:

| Code | Meaning |
|---|---|
| `VALIDATION_ERROR` | A filter or pagination argument is outside its allowed range |
| `UNAUTHORIZED` | API token is missing, expired, revoked or invalid |
| `FORBIDDEN` | API token lacks `database:read` |
| `SERVICE_UNAVAILABLE` | The central PostgreSQL store is temporarily unavailable |
| `QUERY_TOO_COMPLEX` | Weighted query cost exceeds the configured budget |
| `QUERY_TIMEOUT` | Execution exceeded the server deadline |
| `REQUEST_TOO_LARGE` / `RESPONSE_TOO_LARGE` | Transport size budget was exceeded |
| `PERSISTED_QUERY_NOT_FOUND` | The query must first be registered with its document |

The endpoint accepts POST only. It has no mutations, subscriptions, file
uploads, browser IDE or production introspection UI. Default limits are depth
8, 20 aliases, 1,500 document tokens, weighted cost 5,000, 64 KiB request body,
2 MiB response body and an 8-second deadline. Successful public queries are
cached for 60 seconds in a process-local hot cache backed by Redis. Credentialed
queries bypass the shared response cache.

## Persisted Queries

The endpoint implements Apollo Persisted Queries v1. Send
`extensions.persistedQuery.version = 1` and the lowercase SHA-256 document
hash. A hash-only miss returns HTTP 200 with
`PERSISTED_QUERY_NOT_FOUND`; repeat the request with both the same extension and
the full `query`. Later requests can send only the hash. Registrations remain in
Redis for 30 days by default. Both official SDKs perform this handshake
automatically.

## Complete database access

The typed fields above remain public. An authorized integration can also read
every PostgreSQL table and view in `catalog`, `analytics`, `raw`, `platform`
and `hub` by sending `Authorization: Bearer <token>` with the `database:read`
scope. `X-API-Key` remains a migration-compatible header. Identifiers are
checked against database metadata and quoted; arbitrary SQL is never accepted.

```graphql
query CompleteCatalog {
  collections(schemaName: "catalog", limit: 100) {
    items {
      collection
      primaryKey
      estimatedRowCount
      columns { name dataType nullable }
    }
    pageInfo { total }
  }

  records(
    collection: "catalog.constructed_cards"
    fields: ["card_id", "name_ru", "image_url"]
    filters: {collectible: true}
    orderBy: "card_id"
    limit: 50
  ) {
    items
    pageInfo { total hasNextPage }
  }
}
```

All migrated media files are indexed in `platform.media_assets`. Their public
URL is `https://api.kolodahearthstone.com/uploads/` plus `relative_path`.

## REST compatibility

The unified host also exposes both legacy REST contracts during migration:

- former `api.hs-manacost.ru` routes keep the same `/v1/...` paths;
- former `db.kolodahs.ru` routes keep the same `/api/v1/...` paths;
- former database images keep the same `/uploads/...` paths.

The response serializer replaces local `db.kolodahs.ru` media URLs with the
unified hostname.

## Migration policy

The old REST endpoints stay available during the transition. A client is ready
to migrate once all fields it consumes have a GraphQL equivalent and its
production configuration points to the new endpoint. Old DNS records should be
removed only after logs show no required client traffic for an agreed cutover
window.

Until every legacy response has a dedicated typed query, `dataset(sourceId:)`
provides the complete latest imported JSON payload. A fixed `datasetVersion`
can be requested for reproducible integrations. New clients should prefer the
typed fields above because they are smaller and have a more stable contract.
