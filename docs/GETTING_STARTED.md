# Быстрый старт

Это руководство помогает выбрать интерфейс, посмотреть первые данные и
безопасно подключить внешний сервис.

## 1. Выберите способ работы

| Если вам нужно… | Используйте |
| --- | --- |
| Смотреть и сравнивать данные вручную | Веб‑панель |
| Получить готовый набор статистики | REST `/v1/*` или `/datasets/*` |
| Выбрать точные поля и связанные сущности | GraphQL `/v1/` |
| Читать любую таблицу центральной базы | GraphQL с `database:read` |
| Обновлять парсеры или смотреть закрытую диагностику | Токен с `admin` |

## 2. Откройте веб‑панель

Перейдите на <https://api.kolodahearthstone.com/> и войдите через GitHub.
Production разрешает доступ только allowlisted аккаунту.

Начните с раздела **«Обзор и мета»**:

1. На вкладке **«Обзор»** проверьте список источников и время обновления.
2. Откройте **«Мета»** и выберите формат, ранг и период.
3. Нажмите **«Подробнее»** у строки, чтобы увидеть все поля источника.
4. Перейдите к разделам архетипов, Arena, BG‑героев и BG‑существ.

Полное описание интерфейса: [WEB_PANEL.md](WEB_PANEL.md).

## 3. Проверьте публичный API

Liveness:

```bash
curl -fsS https://api.kolodahearthstone.com/health | jq .
```

Реестр источников:

```bash
curl -fsS https://api.kolodahearthstone.com/sources | jq .
```

Один готовый dataset:

```bash
curl -fsS \
  https://api.kolodahearthstone.com/datasets/hsguru_meta_standard_legend \
  | jq '.data.structured'
```

`/health` не подтверждает свежесть каждого источника. Поле состояния и время
обновления смотрите в `/sources`, а операторскую проверку выполняйте через
freshness/quality gates.

## 4. Выполните GraphQL‑запрос

GraphQL принимает `POST` по адресу
`https://api.kolodahearthstone.com/v1/graphql`:

```bash
curl -fsS https://api.kolodahearthstone.com/v1/graphql \
  -H 'Content-Type: application/json' \
  --data '{
    "query": "query { health { status databaseConnected sourceCount latestSyncAt } }"
  }' | jq .
```

Пример поиска карт:

```bash
curl -fsS https://api.kolodahearthstone.com/v1/graphql \
  -H 'Content-Type: application/json' \
  --data '{
    "query": "query { cards(search: \"Reno\", limit: 10) { items { cardId nameRu nameEn manaCost imageUrl } pageInfo { total hasNextPage } } }"
  }' | jq .
```

Typed queries публичны. Поля `collections` и `records`, открывающие полную
PostgreSQL‑базу, требуют токен со scope `database:read`.

## 5. Выпустите токен для интеграции

В панели откройте **Доступ → API‑токены**:

1. Укажите понятное имя конкретного сервиса, например `telegram-bot-prod`.
2. Выберите минимальные права. Для чтения базы обычно достаточно
   `database:read`.
3. Выберите практичный срок действия.
4. Выпустите токен и сразу скопируйте его в secret manager сервиса.

Секрет показывается один раз. Не помещайте его в Git, URL, браузерный JavaScript
или логи.

Проверка токена:

```bash
curl -fsS https://api.kolodahearthstone.com/v1/auth/token \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" | jq .
```

Чтение полной базы:

```bash
curl -fsS https://api.kolodahearthstone.com/v1/graphql \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  --data '{
    "query": "query { collections(schemaName: \"catalog\", limit: 20) { items { collection primaryKey estimatedRowCount } pageInfo { total } } }"
  }' | jq .
```

## 6. Подключите приложение

Минимальный production checklist:

- базовый URL хранится в конфигурации, а не размножен по коду;
- токен хранится только на backend/сервере;
- задан timeout и ограниченный retry для `429` и временных `5xx`;
- pagination обрабатывается явно;
- клиент понимает GraphQL `errors[]` даже при HTTP 200;
- `ETag`/условные запросы используются там, где endpoint их поддерживает;
- приложение сохраняет last-known-good ответ, если это допустимо для продукта;
- monitoring различает недоступность API и устаревший источник.

Продолжение: [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md).

## 7. Запустите проект локально

```bash
git clone https://github.com/Manacost-Labs/api.kolodahearthstone.com.git
cd api.kolodahearthstone.com
make setup
make check
```

Локальный API:

```bash
HS_API_DATA_DIR="$PWD/data" \
HS_API_BIND_HOST="127.0.0.1" \
.venv/bin/python -m app.server
```

Полный refresh не является частью первого запуска: он обращается к внешним
сервисам, может требовать proxy/browser credentials и расходовать платные
лимиты.

## Следующие шаги

- выбрать данные: [DATA_CATALOG.md](DATA_CATALOG.md);
- прочитать REST reference: [API.md](API.md);
- изучить GraphQL: [GRAPHQL_API.md](GRAPHQL_API.md);
- понять состояния данных: [PARSER_PIPELINE.md](PARSER_PIPELINE.md);
- диагностировать проблему: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
