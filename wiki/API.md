# REST и GraphQL API

## Выбор интерфейса

| Задача | Интерфейс |
| --- | --- |
| Готовый статистический срез | REST `/v1/*` |
| Полный raw snapshot | REST `/datasets/{source_id}` |
| Состояние обновлений | REST `/sources` |
| Точные поля и связанные данные | GraphQL `/v1/` |
| Любая таблица центральной базы | GraphQL `collections`/`records` + token |

## REST

Основные public endpoints:

```text
GET /health
GET /sources
GET /sources/{source_id}
GET /datasets
GET /datasets/{source_id}
GET /v1/constructed/*
GET /v1/battlegrounds/*
GET /v1/arena/*
GET /v1/system/*
```

Пример:

```bash
curl -fsS \
  'https://api.kolodahearthstone.com/v1/constructed/decks?class_name=Mage&limit=50' \
  | jq .
```

## GraphQL

Endpoint:

```text
POST https://api.kolodahearthstone.com/v1/graphql
Content-Type: application/json
```

Основные query roots:

| Field | Данные |
| --- | --- |
| `health` | PostgreSQL и sync state |
| `cards` / `card` | Constructed и BG cards |
| `battlegroundHeroes` | Герои, art, powers, buddies |
| `statistics` | Нормализованная статистика |
| `statisticHistory` / `compareStatisticPatches` | История и сравнение патчей |
| `archetypes` | Архетипы по формату/рангу/региону |
| `battlegroundMinions` | BG minions по tier/MMR/time range |
| `sources` / `datasets` | Интеграции и snapshots |
| `search` | Поиск карт, существ, героев, архетипов и источников |
| `collections` / `records` | Полная PostgreSQL‑база |

Пример:

```graphql
query MetaRows {
  statistics(
    domain: "constructed"
    formatName: "wild"
    rankRange: "legend"
    limit: 20
  ) {
    items { sourceId entityType name games winRate popularity fetchedAt }
    pageInfo { total hasNextPage }
  }
}
```

## Полная база

`collections` и `records` требуют `database:read`:

```graphql
query CompleteDatabase {
  collections(schemaName: "catalog", limit: 100) {
    items {
      collection
      primaryKey
      estimatedRowCount
      columns { name dataType nullable }
    }
    pageInfo { total }
  }
}
```

Произвольный SQL не принимается. Collection/field identifiers проверяются по
metadata allowlist и безопасно quote-ятся.

## Pagination и ограничения

- response: `items` + `pageInfo`;
- обычный `limit`: 50–100;
- максимум `limit`: 200;
- максимум `offset`: 100 000;
- `nextCursor`/`after` доступны у всех больших коллекций;
- GraphQL ограничен depth, weighted cost, размером request/response и timeout;
- Apollo Persisted Queries v1 поддерживаются для мобильных клиентов;
- mutations/subscriptions/uploads отсутствуют.

## Ошибки

GraphQL application error возвращает код в `errors[].extensions.code`:

- `VALIDATION_ERROR`;
- `UNAUTHORIZED`;
- `FORBIDDEN`;
- `SERVICE_UNAVAILABLE`.

Клиент должен проверять `errors[]` даже при HTTP 200.

Полные references: [REST](../docs/API.md),
[GraphQL](../docs/GRAPHQL_API.md),
[integration guide](../docs/INTEGRATION_GUIDE.md).
