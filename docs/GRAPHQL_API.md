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
| `archetypes` | Latest archetype data split by rank, format and region |
| `battlegroundMinions` | Minion performance split by tier, MMR and time range |
| `sources` | Integrated sources and their last update state |
| `datasets` | Latest imported version and payload size for each source |
| `dataset` | One complete raw dataset for compatibility during migration |
| `collections` | Every table/view and its columns; requires `database:read` |
| `records` | Paginated rows from any collection; requires `database:read` |

Every collection returns `items` and `pageInfo`. `limit` defaults to 50 or 100
and cannot exceed 200. `offset` cannot exceed 100,000.

`cards` additionally supports keyset pagination. Read `pageInfo.nextCursor` and
pass it as `after` in the next query. Do not combine `after` with a non-zero
`offset`; keep all filters unchanged between pages. The cursor is opaque and
must be stored and returned without decoding or editing it.

```graphql
query NextCardsPage($after: String) {
  cards(limit: 50, after: $after) {
    items { cardId nameRu imageUrl }
    pageInfo { hasNextPage nextCursor }
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

The endpoint accepts POST only. It has no mutations, subscriptions, file
uploads, browser IDE or production introspection UI. GraphQL documents are
limited to depth 8, 20 aliases and 1,500 tokens.

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
