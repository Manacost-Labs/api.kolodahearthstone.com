# Koloda Hearthstone API

## Base URLs

| Назначение | URL |
| --- | --- |
| Production | `https://api.kolodahearthstone.com` |
| GraphQL | `https://api.kolodahearthstone.com/v1/graphql` |
| Типизированный REST v1 | `https://api.kolodahearthstone.com/v1` |
| REST базы карт и библиотек | `https://api.kolodahearthstone.com/api/v1` |
| Raw datasets | `https://api.kolodahearthstone.com/datasets` |
| Изображения | `https://api.kolodahearthstone.com/uploads` |
| OpenAPI | `https://api.kolodahearthstone.com/openapi.json` |
| Swagger UI | `https://api.kolodahearthstone.com/docs` |
| ReDoc | `https://api.kolodahearthstone.com/redoc` |
| Веб‑панель | `https://api.kolodahearthstone.com/` |

Все ответы API используют UTF‑8 и JSON, кроме endpoint изображения. Только
HTTPS считается каноническим transport.

## Авторизация

Публичные `GET` endpoints не требуют токена. Закрытые операции принимают:

```http
Authorization: Bearer khs_v1_<token-id>_<secret>
```

`X-API-Key` временно поддерживается для совместимости. Не отправляйте оба
заголовка с разными значениями.

| Доступ | Что разрешает |
| --- | --- |
| Public | Публичные каталоги, статистика, datasets и typed GraphQL queries |
| Любой действующий токен | `GET /v1/auth/token` |
| `database:read` | GraphQL `collections` и `records` |
| `admin` | `/admin/*`, `/ops/*` и `/health/premium`, кроме управления токенами |
| `tokens:manage` | Выпуск, список и отзыв API‑токенов |
| `X-Orchestrator-Key` | Только `/admin/orchestrator/*` |

## GraphQL

### Endpoint

| Метод | Path | Доступ | Назначение |
| --- | --- | --- | --- |
| POST | `/v1/graphql` | Public / `database:read` для полной базы | Канонический GraphQL endpoint |
| POST | `/v1/` | Public / `database:read` для полной базы | Deprecated GraphQL alias |

```bash
curl -fsS https://api.kolodahearthstone.com/v1/graphql \
  -H 'Content-Type: application/json' \
  --data '{"query":"query { health { status databaseConnected sourceCount latestSyncAt } }"}'
```

### Query roots

| Query | Доступ | Данные |
| --- | --- | --- |
| `health` | Public | Состояние PostgreSQL и синхронизации |
| `cards`, `card` | Public | Constructed и Battlegrounds cards |
| `battlegroundHeroes` | Public | BG‑герои, изображения, силы героев и buddies |
| `statistics` | Public | Нормализованная статистика режимов и сущностей |
| `statisticHistory` | Public | История показателей одной сущности по snapshots и патчам |
| `compareStatisticPatches` | Public | Сравнение метрик сущности между двумя патчами |
| `archetypes` | Public | Архетипы по формату, рангу и региону |
| `battlegroundMinions` | Public | BG‑существа по tier, MMR и периоду |
| `sources` | Public | Источники и состояние синхронизации |
| `search` | Public | Единый поиск карт, существ, героев, архетипов и источников |
| `datasets`, `dataset` | Public | Версии и полные snapshots datasets |
| `collections` | `database:read` | Таблицы/views, колонки и primary keys |
| `records` | `database:read` | Строки любой разрешённой PostgreSQL collection |

GraphQL pagination возвращает `items` и `pageInfo`. Максимальный `limit` —
`200`, максимальный `offset` — `100000`. Все большие коллекции возвращают
`pageInfo.nextCursor`, который передаётся в `after` для быстрой глубокой
пагинации. Ошибки находятся в `errors[].extensions.code`.

## REST v1 — Constructed, Battlegrounds и Arena

Все endpoints этого раздела публичные.

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/v1/constructed/hsguru-deck` | `archetype` (required), `format_name`, `rank` | Точные HSGuru decks архетипа |
| GET | `/v1/constructed/decks` | `class_name`, `format_name`, `source_id`, `min_win_rate`, `q`, `limit`, `offset` | Поиск колод |
| GET | `/v1/constructed/archetypes` | `class_name`, `q`, `rank_range`, `game_type`, `limit`, `offset` | HSReplay archetypes |
| GET | `/v1/battlegrounds/heroes` | `mode`, `q`, `limit`, `offset` | BG‑герои Solo/Duos |
| GET | `/v1/battlegrounds/minions` | `q`, `tavern_tier`, `limit`, `offset` | BG‑существа |
| GET | `/v1/bg/heroes` | `mode`, `q`, `limit`, `offset` | Deprecated alias BG‑героев |
| GET | `/v1/bg/minions` | `q`, `tavern_tier`, `limit`, `offset` | Deprecated alias BG‑существ |
| GET | `/v1/arena/classes` | `source_id`, `limit`, `offset` | Статистика классов Arena |

## REST v1 — HSGuru

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/v1/hsguru/meta` | `format`, `rank`, `period`, `coin`, `min_games` | Meta Standard/Wild |
| GET | `/v1/hsguru/archetypes` | `format`, `q`, `min_games`, `has_decks`, `sort`, `order`, `limit`, `offset` | Текущий каталог архетипов |
| GET | `/v1/hsguru/archetypes/history` | `archetype` (required), `format` (required), `limit` | История архетипа |
| GET | `/v1/hsguru/archetypes/analysis` | `archetype` (required), `format` (required) | Полный анализ архетипа |

## REST v1 — System

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/v1/sources` | `site`, `category` | Канонический реестр источников |
| GET | `/v1/datasets` | — | Канонический список datasets |
| GET | `/v1/health` | — | Канонический health и freshness summary |
| GET | `/v1/system/sources` | `site`, `category` | Deprecated alias реестра источников |
| GET | `/v1/system/datasets` | — | Deprecated alias списка datasets |
| GET | `/v1/system/health` | — | Deprecated alias health summary |
| GET | `/v1/system/parsing-reliability` | — | Reliability за 24h, 7d и 30d |
| GET | `/v1/auth/token` | — | Identity, scopes и срок текущего токена |

В reliability-окнах `eligible_attempts` равен сумме
`observed_eligible_attempts + missing_terminal_windows`. Missing terminal
учитывается только когда в уже записанном логическом refresh ожидаемых sources
больше, чем distinct terminal rows. Runs с одинаковым `refresh_window_id`
сворачиваются вместе, поэтому успешный recovery не удваивает знаменатель.
Записанный `skipped` остаётся в `counts.skipped`, исключается из SLO и не
считается потерянным terminal.
Каждое окно также содержит `scheduled_reliability`: долговечный журнал заранее
создаёт слоты «расписание × источник × дедлайн» и поэтому видит даже запуск,
который вообще не начался. `missing`, `late`, `on_time_nonfresh` и
`on_time_fresh` взаимно исключают друг друга; отключённые источники сохраняются
как явные `excluded_slots`. Сейчас журнал подключён к двум основным ежедневным
Docker-расписаниям (`refresh-all-daily` и `refresh-api-daily`), поэтому его
`ledger_status` остаётся `partial`, а процент показывается как предварительный.
После полного охвата всех primary timers и накопления выбранного периода статус
станет `covered`/`observed`. Старый headline SLO до этого не объявляется
окончательным.

Каждое окно также содержит `verified_completeness`. Этот блок отделяет
доступность резервного snapshot от доказанного получения нового кандидата без
необъяснимых потерь при нормализации. Взвешенный процент попыток показывается
отдельно от невзвешенного среднего по источникам, худшего наблюдавшегося
источника и доли источников, которые сами выполняют цель 99%. Округлённое для
интерфейса значение не участвует в решении: граница сравнивается по исходным
счётчикам и точным per-source дробям; результат macro-gate публикуется отдельно
как `macro_target_met`.

Отдельный `parsesunix_rollout` показывает внедрение нового ядра и не смешивается
с главным SLO: direct/shadow/active, проверка транспорта, пригодность кандидата
и подтверждённая публикация считаются раздельно. Платный Scrape.do возможен
только после `BLOCKED/SOFT_BLOCK`, при явном source allowlist и двух ненулевых
лимитах; default остаётся полностью бесплатным. Долговечный ledger резервирует
worst-case credits до запроса, неизвестная стоимость останавливает расходы, а
один URL не передаётся повторно в старую платную цепочку. Bright Data этим
rollout не включается.

Контракт постепенно вводится по каталогу: `instrumented_sources` и
`catalog_sources` всегда показывают фактический охват. Пока хотя бы один из
трёх coverage gates ниже 99%, наблюдалось менее 99% инструментированных
источников, schedule ledger не покрывает всё окно или менее 99% источников
выполняют собственную цель, `objective_status` остаётся `collecting` либо
`miss`, но не `met`.

Для HSReplay проверка доказывает свежесть представления upstream и отсутствие
потерь между полученным ответом и опубликованными строками. Она не утверждает,
что upstream вернул весь возможный каталог, если endpoint не публикует
канонический `total`/pagination или независимый baseline; такой population
coverage помечается как `unverifiable`. Реально отсутствующая метрика с
разрешённой детерминированной причиной учитывается как полученная, но не
увеличивает отдельный `metric_availability_score`.

## Sources и raw datasets

| Метод | Path | Query parameters | Доступ | Назначение |
| --- | --- | --- | --- | --- |
| GET | `/health` | — | Public | Liveness API |
| GET | `/sources` | `site`, `category` | Public | Все источники и их status |
| GET | `/sources/{source_id}` | — | Public | Один источник |
| GET | `/datasets` | — | Public | Наличие datasets по всем источникам |
| GET | `/datasets/{source_id}` | — | Public | Полный опубликованный dataset |
| HEAD | `/datasets/{source_id}` | — | Public | Проверка dataset без body |
| GET | `/demo/overview` | — | Public | Агрегированный обзор источников |
| GET | `/demo/view/{source_id}` | — | Public | Нормализованное представление dataset |
| GET | `/system/technologies` | — | Public | Используемые parser technologies |
| GET | `/firecrawl/hsreplay/map` | — | Public | Сохранённая карта HSReplay URLs |
| GET | `/firecrawl/hsreplay/index` | — | Public | Производный HSReplay index |

`firestone_standard` отключён по умолчанию. Только после получения письменного
разрешения Firestone/ZeroToHeroes оператор может явно установить
`HS_FIRESTONE_STANDARD_AUTHORIZED=true`. Пока флаг выключен, источник не делает
сетевых запросов, исключён из scheduled refresh и denominator проверки 99%, а
ранее сохранённый snapshot скрыт из raw dataset, demo и REST v1. Операционный
health и stale-monitor также не считают такой источник неисправным.

`/health` проверяет доступность API, но не гарантирует свежесть всех datasets.
Для freshness используйте `/v1/sources` или `/v1/health`.

## REST базы — `/api/v1`

Эти endpoints публичные и read-only. Все `GET` маршруты также поддерживают
`HEAD`; CORS preflight поддерживает `OPTIONS`.

Объекты с доступным артом содержат отдельный `images.horizontal`: публичный
URL детерминированного crop `320×64` в WebP. Исходное изображение при этом не
заменяется. Поле может быть `null`, если у объекта нет подходящего арта.

### API index и metadata

| Метод | Path | Назначение |
| --- | --- | --- |
| GET | `/api` | Краткий index совместимого API |
| GET | `/api/v1` | Версия, счётчики и список database endpoints |
| GET | `/api/v1/meta` | Типы, tiers, библиотеки и общие счётчики |

### Battlegrounds cards

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/v1/cards` | `q`, `tier`, `dbf`, `in_pool`, `duos_only`, `card_type`, `creature_type`, `include`, `include_variants`, `updated_since`, `page`, `per_page` | Список BG cards |
| GET | `/api/v1/cards/{card_id}` | — | Карта по `card_id` |
| GET | `/api/v1/cards/{card_id}/wiki` | — | Wiki‑метаданные карты |
| GET | `/api/v1/cards/by-dbf/{dbf}` | — | Карта по `dbf` |
| GET | `/api/v1/cards/by-dbf/{dbf}/wiki` | — | Wiki‑метаданные карты по `dbf` |

### Constructed cards

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/v1/constructed-cards` | `q`, `format`, `dbf`, `collectible`, `card_type`, `class`, `set`, `include`, `updated_since`, `page`, `per_page` | Карты Standard/Wild |
| GET | `/api/v1/constructed-cards/{card_id}` | — | Карта по `card_id` |
| GET | `/api/v1/constructed-cards/{card_id}/wiki` | — | Wiki‑метаданные карты |
| GET | `/api/v1/constructed-cards/by-dbf/{dbf}` | — | Карта по `dbf` |
| GET | `/api/v1/constructed-cards/by-dbf/{dbf}/wiki` | — | Wiki‑метаданные по `dbf` |
| GET | `/api/v1/diamond-cards` | `q`, `format`, `section`, `has_animated`, `updated_since`, `page`, `per_page` | Алмазные карты |
| GET | `/api/v1/diamond-cards/{card_id}` | — | Алмазная карта по base/diamond `card_id` |

### Heroes, skins, pets и coins

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/v1/heroes` | `q`, `dbf`, `updated_since`, `page`, `per_page` | BG‑герои |
| GET | `/api/v1/heroes/{card_id}` | — | Герой по `card_id` |
| GET | `/api/v1/heroes/by-dbf/{dbf}` | — | Герой по `dbf` |
| GET | `/api/v1/hero-skins` | `q`, `dbf`, `class`, `category`, `rarity`, `has_animated`, `has_gallery`, `has_sounds`, `view`, `updated_since`, `page`, `per_page` | Скины героев |
| GET | `/api/v1/hero-skins/{card_id}` | — | Скин по `card_id` |
| GET | `/api/v1/hero-skins/by-dbf/{dbf}` | — | Скин по `dbf` |
| GET | `/api/v1/pets` | `q`, `dbf`, `pet_id`, `level`, `has_gallery`, `has_background`, `view`, `updated_since`, `page`, `per_page` | Питомцы |
| GET | `/api/v1/pets/{card_id}` | — | Питомец по `card_id` |
| GET | `/api/v1/pets/by-dbf/{dbf}` | — | Питомец по `dbf` |
| GET | `/api/v1/coins` | `q`, `dbf`, `view`, `updated_since`, `page`, `per_page` | Косметические монетки |
| GET | `/api/v1/coins/{card_id}` | — | Монетка по `card_id` |
| GET | `/api/v1/coins/by-dbf/{dbf}` | — | Монетка по `dbf` |

### Timewarped cards

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/v1/timewarped-cards` | `q`, `tier`, `dbf`, `card_type`, `updated_since`, `page`, `per_page` | Хрономальные карты |
| GET | `/api/v1/timewarped-cards/{card_id}` | — | Карта по `card_id` |
| GET | `/api/v1/timewarped-cards/by-dbf/{dbf}` | — | Карта по `dbf` |
| GET | `/api/v1/chronomal-cards` | Те же параметры | Alias списка хрономальных карт |
| GET | `/api/v1/chronomal-cards/{card_id}` | — | Alias detail по `card_id` |
| GET | `/api/v1/chronomal-cards/by-dbf/{dbf}` | — | Alias detail по `dbf` |

### Battlegrounds libraries

Допустимые значения `{library}`: `anomaly`, `dark_gift`, `quest`,
`darkmoon_prize`, `reward`, `trinket` и их plural aliases.

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/v1/libraries/{library}` | `q`, `dbf`, `in_pool`, `status`, `group`, `tier`, `updated_since`, `page`, `per_page` | Список выбранной библиотеки |
| GET | `/api/v1/libraries/{library}/{card_id}` | — | Запись по `card_id` |
| GET | `/api/v1/libraries/{library}/by-dbf/{dbf}` | — | Запись по `dbf` |
| GET | `/api/v1/anomalies` | Library filters | Аномалии |
| GET | `/api/v1/anomalies/{card_id}` | — | Аномалия по `card_id` |
| GET | `/api/v1/anomalies/by-dbf/{dbf}` | — | Аномалия по `dbf` |
| GET | `/api/v1/dark-gifts` | Library filters | Тёмные дары |
| GET | `/api/v1/dark-gifts/{card_id}` | — | Тёмный дар по `card_id` |
| GET | `/api/v1/dark-gifts/by-dbf/{dbf}` | — | Тёмный дар по `dbf` |
| GET | `/api/v1/quests` | Library filters | Квесты |
| GET | `/api/v1/quests/{card_id}` | — | Квест по `card_id` |
| GET | `/api/v1/quests/by-dbf/{dbf}` | — | Квест по `dbf` |
| GET | `/api/v1/darkmoon-prizes` | Library filters | Призы Ярмарки Новолуния |
| GET | `/api/v1/darkmoon-prizes/{card_id}` | — | Приз по `card_id` |
| GET | `/api/v1/darkmoon-prizes/by-dbf/{dbf}` | — | Приз по `dbf` |
| GET | `/api/v1/rewards` | Library filters | Награды |
| GET | `/api/v1/rewards/{card_id}` | — | Награда по `card_id` |
| GET | `/api/v1/rewards/by-dbf/{dbf}` | — | Награда по `dbf` |
| GET | `/api/v1/trinkets` | Library filters | Аксессуары |
| GET | `/api/v1/trinkets/{card_id}` | — | Аксессуар по `card_id` |
| GET | `/api/v1/trinkets/by-dbf/{dbf}` | — | Аксессуар по `dbf` |

## Специализированная статистика и базы

Все endpoints этого раздела публичные.

### Decks, archetypes и card trends

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/db/decks` | `class_name`, `format_name`, `source_id`, `min_win_rate`, `q`, `limit`, `offset` | Поиск колод |
| GET | `/api/db/archetypes` | `class_name`, `q`, `rank_range`, `game_type`, `limit`, `offset` | Список archetype snapshots |
| GET | `/api/db/archetypes/{archetype_id}` | `rank_range`, `game_type` | Архетип и основная статистика |
| GET | `/api/db/archetypes/{archetype_id}/mulligan` | `rank_range`, `game_type`, `display_only`, `limit` | Mulligan карты |
| GET | `/api/db/archetypes/{archetype_id}/matchups` | `rank_range`, `game_type`, `min_games`, `limit` | Matchups |
| GET | `/api/db/archetypes/{archetype_id}/decks` | `rank_range`, `game_type`, `include_cards`, `limit` | Decks архетипа |
| GET | `/api/db/archetypes/{archetype_id}/history` | `rank_range`, `game_type` | История архетипа |
| GET | `/api/db/cards/trends` | `card_name` (required), `source_id`, `class_name`, `limit` | Ranked trends карты |
| GET | `/api/hsreplay/archetypes` | `hl` | Словарь HSReplay archetypes |

### Battlegrounds minions, heroes и trinkets

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/db/bg/minions` | `q`, `tavern_tier`, `limit`, `offset` | BG minion snapshots |
| GET | `/api/db/bg/minions/{dbf_id}` | — | Последняя запись существа |
| GET | `/api/db/bg/minions/{dbf_id}/history` | `limit` | История существа |
| GET | `/api/bg/heroes` | `mode`, `q`, `mmr` | BG hero statistics |
| GET | `/api/bg/heroes/duos` | `q` | BG Duos heroes |
| GET | `/api/bg/heroes/{dbf_id}` | — | Полная запись героя |
| GET | `/api/bg/heroes/{dbf_id}/tavern-up` | — | Статистика tavern-up |
| GET | `/api/bg/heroes/{dbf_id}/hero-power` | — | Статистика hero power |
| GET | `/api/bg/heroes/{dbf_id}/best-composition` | — | Лучшая composition героя |
| GET | `/api/bg/trinkets` | `trinket_tier`, `active_only`, `mmr`, `timeRange` | Статистика trinkets |

### Patches и compositions

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/api/patches` | `q`, `match_state`, `include_content`, `limit`, `offset` | Список патчей Hearthstone |
| GET | `/api/patches/{version}` | `include_content` | Один патч |
| GET | `/api/bg/compositions/screenshot/latest` | — | Metadata последнего BG screenshot |
| GET | `/api/bg/compositions/screenshot/latest/image` | — | Файл последнего BG screenshot |

## API‑токены

| Метод | Path | Доступ | Назначение |
| --- | --- | --- | --- |
| GET | `/v1/auth/token` | Любой действующий токен | Проверить identity и scopes |
| POST | `/admin/api-tokens` | `tokens:manage` | Выпустить токен |
| GET | `/admin/api-tokens` | `tokens:manage` | Список токенов без secrets |
| GET | `/admin/api-tokens/{token_id}/usage` | `tokens:manage` | Статистика токена за выбранный месяц |
| DELETE | `/admin/api-tokens/{token_id}` | `tokens:manage` | Немедленно отозвать токен |

## Admin и parser control

Все endpoints этого раздела требуют scope `admin`.

| Метод | Path | Parameters / body | Назначение |
| --- | --- | --- | --- |
| GET | `/admin/parser-control` | — | Политика, sections и schedules |
| PATCH | `/admin/parser-control/policy` | JSON body | Обновить policy |
| PATCH | `/admin/parser-control/sections` | JSON body | Обновить sections |
| POST | `/admin/parser-runs` | JSON body | Создать parser run |
| GET | `/admin/parser-runs` | `limit` | Список parser runs |
| POST | `/admin/refresh` | `source_id` | Refresh одного или всех sources |
| POST | `/admin/refresh/hsreplay-archetypes` | `limit`, `rank_range`, `game_type`, `region` | Обновить HSReplay archetype DB |
| POST | `/admin/refresh/bg-minions-db` | — | Обновить BG minion DB |
| POST | `/admin/refresh/bg-hero-details` | `limit`, `concurrency`, `mmr`, `time_range` | Обновить BG hero details |
| POST | `/admin/refresh/hsguru-meta-matrix` | `concurrency` | Обновить HSGuru meta matrix |
| POST | `/admin/refresh/hsguru-archetype-analysis` | `concurrency`, `limit` | Обновить HSGuru analysis |
| POST | `/admin/capture/bg-compositions-screenshot` | — | Создать проверенный BG screenshot |
| GET | `/admin/datasets/{source_id}/quarantine` | `limit` | Карантин кандидатов dataset |
| POST | `/admin/datasets/{source_id}/publication/rollback` | JSON body | Rollback опубликованного dataset |
| PUT | `/admin/datasets/{source_id}` | JSON body | Загрузить dataset вручную |
| GET | `/health/premium` | `live` | Проверить premium authentication |

## Ops

Все endpoints этого раздела требуют scope `admin`.

| Метод | Path | Query parameters | Назначение |
| --- | --- | --- | --- |
| GET | `/ops/health` | — | Полная диагностика sources и storage |
| GET | `/ops/summary` | `since_hours` | Сводка freshness/jobs |
| GET | `/ops/events` | `limit`, `source_id`, `event`, `action`, `action_group`, `level`, `trace_id`, `run_id`, `since_hours` | Фильтрованные события |
| GET | `/ops/trace/{trace_id}` | — | События trace |
| GET | `/ops/run/{run_id}` | — | Состояние job run |

## External orchestrator

Эти endpoints принимают только отдельный header `X-Orchestrator-Key`.

| Метод | Path | Parameters / body | Назначение |
| --- | --- | --- | --- |
| POST | `/admin/orchestrator/parser-runs` | JSON body | Идемпотентно создать scoped run |
| GET | `/admin/orchestrator/parser-runs/{run_id}` | — | Минимальный status конкретного run |

## Служебные и UI endpoints

| Метод | Path | Доступ | Назначение |
| --- | --- | --- | --- |
| GET | `/` | GitHub OAuth | Веб‑панель базы |
| GET | `/ui` | Public | Встроенный dataset viewer |
| GET | `/ui/logs` | Public UI | Интерфейс событий и логов |
| GET | `/ui/technologies` | Public UI | Интерфейс parser technologies |
| GET | `/openapi.json` | Public | OpenAPI 3 schema |
| GET | `/docs` | Public | Swagger UI |
| GET | `/docs/oauth2-redirect` | Public | Swagger OAuth redirect |
| GET | `/redoc` | Public | ReDoc UI |
| GET | `/uploads/{path}` | Public | Изображения и media assets |

## Общие HTTP статусы

| Status | Значение |
| ---: | --- |
| `200` | Успешный ответ |
| `204` | Успешный `OPTIONS` без body |
| `304` | Ресурс не изменился по `ETag`/`Last-Modified` |
| `400` | Неверный запрос или конфликт credentials |
| `401` | Токен отсутствует, неверен, истёк или отозван |
| `403` | Недостаточный scope |
| `404` | Endpoint или сущность не найдены |
| `409` | Конфликт состояния/публикации |
| `422` | Ошибка параметров или JSON body |
| `429` | Rate limit |
| `502` | Ошибка upstream |
| `503` | Dataset или центральная база временно недоступны |

## Минимальные примеры

```bash
# Public REST
curl -fsS https://api.kolodahearthstone.com/v1/sources

# Public database REST
curl -fsS 'https://api.kolodahearthstone.com/api/v1/cards?per_page=20&tier=6'

# Private GraphQL database
curl -fsS https://api.kolodahearthstone.com/v1/graphql \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" \
  -H 'Content-Type: application/json' \
  --data '{"query":"query { collections(schemaName: \"catalog\", limit: 20) { items { collection estimatedRowCount } pageInfo { total } } }"}'
```
