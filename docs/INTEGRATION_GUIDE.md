# Руководство по интеграции

Цель этого руководства — подключить внешний сервис к
`api.kolodahearthstone.com` без зависимости от закрываемых старых доменов и без
утечки токена.

## Базовые адреса

| Интерфейс | Адрес |
| --- | --- |
| GraphQL | `POST https://api.kolodahearthstone.com/v1/` |
| Типизированный REST | `https://api.kolodahearthstone.com/v1/...` |
| Raw datasets | `https://api.kolodahearthstone.com/datasets/{source_id}` |
| Состояние источников | `https://api.kolodahearthstone.com/sources` |
| Liveness | `https://api.kolodahearthstone.com/health` |

Храните origin в одной переменной конфигурации. Не оставляйте в коде ссылки на
`api.hs-manacost.ru` или `db.kolodahs.ru`.

## Как выбрать API

### REST

REST подходит, если:

- уже есть готовый endpoint с нужной выборкой;
- нужен стабильный простой JSON для кеша или frontend;
- вы переносите существующего клиента без изменения его data model;
- нужен целиком исходный parser snapshot.

### GraphQL

GraphQL подходит, если:

- клиенту нужны только конкретные поля;
- нужно объединить каталоги и статистику одним запросом;
- нужна единая pagination model;
- интеграции требуется просматривать произвольные таблицы/представления
  центральной базы через `collections` и `records`.

Typed GraphQL queries публичны. Complete database access требует
`database:read`.

## Авторизация

Передавайте токен только в заголовке:

```http
Authorization: Bearer khs_v1_<token-id>_<secret>
```

`X-API-Key` поддерживается на время миграции, но новые клиенты должны
использовать Bearer. Если клиент отправляет оба заголовка с разными значениями,
API отвечает ошибкой `AMBIGUOUS_CREDENTIALS`.

Правила хранения:

- backend: environment/secret manager с закрытыми правами;
- CI: encrypted secret, не plaintext variable в репозитории;
- frontend/mobile: не встраивать privileged token в bundle;
- логи: удалять `Authorization` и URL query string;
- один consumer — один token, чтобы отзыв не затронул остальные сервисы.

## Пример GraphQL‑клиента

Запрос полного каталога:

```graphql
query CompleteCatalog {
  collections(schemaName: "catalog", limit: 100) {
    items {
      collection
      primaryKey
      estimatedRowCount
      columns { name dataType nullable }
    }
    pageInfo { total hasNextPage }
  }
}
```

Чтение записей:

```graphql
query ConstructedCards {
  records(
    collection: "catalog.constructed_cards"
    fields: ["card_id", "name_ru", "name_en", "image_url"]
    filters: {collectible: true}
    orderBy: "card_id"
    limit: 100
    offset: 0
  ) {
    items
    pageInfo { total hasNextPage }
  }
}
```

HTTP:

```bash
curl -fsS https://api.kolodahearthstone.com/v1/ \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  --data-binary @request.json
```

GraphQL может вернуть HTTP 200 вместе с непустым `errors[]`. Клиент должен
проверять и HTTP status, и GraphQL error envelope.

## Pagination

Collections возвращают `items` и `pageInfo`. Используйте `limit` и `offset` и
продолжайте чтение, пока `hasNextPage=true`.

- типичный `limit`: 50–100;
- верхний предел: 200;
- `offset` ограничен 100 000;
- не загружайте всю базу одним запросом;
- для регулярной синхронизации предпочитайте стабильный порядок и сохраняйте
  версию/время полученного dataset.

## Ошибки и retry

| Ситуация | Действие клиента |
| --- | --- |
| `400/422` | Исправить query/параметры; не повторять без изменения |
| `401` | Проверить формат, срок и отзыв токена |
| `403` | Выпустить токен с нужным scope; не расширять права автоматически |
| `429` | Уважать `Retry-After`, использовать jitter |
| `502/503/504` | Ограниченный exponential backoff |
| GraphQL `SERVICE_UNAVAILABLE` | Сохранить LKG у клиента и повторить позднее |

Не делайте бесконечный retry и не превращайте все `4xx` в повторяемые ошибки.

## Кеширование и свежесть

Dataset endpoint может поддерживать `ETag`; отправляйте `If-None-Match`, чтобы
не скачивать неизменившийся payload. Храните:

- source/dataset id;
- dataset version или upstream `fetched_at`;
- время получения вашим сервисом;
- последний проверенный payload, если продукт допускает LKG.

Состояние `ok_cached` означает, что API осознанно сохранил прошлый валидный
срез после ошибки refresh. Это полезные данные, но мониторинг должен отличать
их от свежей публикации.

## Версионирование

- новые интеграции используют `api.kolodahearthstone.com`;
- `/v1/*` — основной namespace типизированных REST endpoints;
- `POST /v1/` — GraphQL;
- legacy REST paths временно сохранены на новом host для миграции;
- raw `dataset` полезен для совместимости, typed fields предпочтительнее для
  долгоживущего контракта.

## Checklist переноса со старых доменов

- [ ] Все origins заменены на `https://api.kolodahearthstone.com`.
- [ ] Старые `/v1/...` или `/api/v1/...` paths проверены на новом host.
- [ ] Для private database access выпущен отдельный `database:read` token.
- [ ] Токен хранится на сервере и не попадает в browser bundle.
- [ ] Клиент обрабатывает pagination, timeout, `429/5xx` и GraphQL errors.
- [ ] Media URLs используют новый `/uploads/...` host.
- [ ] Monitoring проверяет не только `/health`, но и важные source states.
- [ ] Логи за согласованное окно не показывают обязательного трафика на старые
      домены.
- [ ] Только после этого старые DNS/приложения можно отключать.

## Ссылки

- [Каталог данных](DATA_CATALOG.md)
- [REST API](API.md)
- [GraphQL API](GRAPHQL_API.md)
- [API‑токены](API_TOKENS.md)
- [Диагностика](TROUBLESHOOTING.md)
