# Hearthstone Parses API

Актуальная документация REST API для `hearthstone-parses`.

Практический каталог наборов, полей и готовых запросов:
[DATA_CATALOG.md](DATA_CATALOG.md).

Production base URL:

```text
https://api.hs-manacost.ru
```

Локально:

```text
http://127.0.0.1:8000
```

## Auth

Публичные read-only endpoints доступны без ключа. Операционные и admin endpoints требуют:

```http
X-API-Key: <HS_API_KEY>
```

`HS_API_KEY` хранится только в `/etc/hs-data-api.env`.

## Public Endpoints

Сохранённое поле `state` принимает только значения `ok`, `partial`, `fetch_error`, `http_error`, `blocked_by_protection`, `proxy_required`, `quality_error`, `timed_out`, `never_fetched`. Значение `ok_cached` встречается только в вычисляемом `effective_state`, когда API продолжает безопасно отдавать предыдущий успешный dataset после неудачного refresh.

| Method | Path | Назначение |
| --- | --- | --- |
| `GET` | `/health` | Лёгкий liveness API. Не раскрывает список источников, пути и premium auth детали. |
| `GET` | `/sources` | Список источников с URL, категорией, статусом и наличием dataset. |
| `GET` | `/sources/{source_id}` | Метаданные одного источника. |
| `GET` | `/datasets` | Список источников и наличие сохранённого dataset. |
| `GET` | `/datasets/{source_id}` | Основной endpoint данных: статус refresh и `data.structured`. |
| `GET` | `/demo/overview` | Сводка для UI. |
| `GET` | `/demo/view/{source_id}` | Подготовленное представление одного источника для UI. |
| `GET` | `/system/technologies` | Публичная техническая карточка источников и parser stack без секретов. |
| `GET` | `/ui` | Web UI. |
| `GET` | `/ui/logs` | UI логов. |
| `GET` | `/ui/technologies` | UI страницы технологий. |
| `GET` | `/api/bg/trinkets` | Объединенный BG endpoint малых/больших аксессуаров с tier и race variants. |
| `GET` | `/api/db/decks` | SQL-backed поиск колод. |
| `GET` | `/api/db/cards/trends` | SQL-backed история популярности карт. |
| `GET` | `/api/db/bg/minions` | SQL-backed последние snapshots BG существ HSReplay. |
| `GET` | `/api/db/bg/minions/{dbfId}` | Детали BG существа: summary + combat-round stats. |
| `GET` | `/api/db/bg/minions/{dbfId}/history` | Time series для графиков BG существа. |
| `GET` | `/api/bg/heroes` | HSReplay BG hero tier list: `mode=solo` или `mode=duos`. |
| `GET` | `/api/bg/heroes/duos` | Быстрый alias для duos tier list без best composition. |
| `GET` | `/api/bg/heroes/{dbfId}` | Детали solo-героя: таверна, hero power, combat WR, составы. |
| `GET` | `/api/bg/heroes/{dbfId}/tavern-up` | Только статистика "когда улучшать таверну". |
| `GET` | `/api/bg/heroes/{dbfId}/hero-power` | Только статистика "когда прожимать силу героя". |
| `GET` | `/api/bg/heroes/{dbfId}/best-composition` | Лучший состав героя и топ составов. |
| `GET` | `/api/patches` | SQLite-backed база патчей Hearthstone с привязкой к hs-manacost.ru и wiki. |
| `GET` | `/api/patches/{version}` | Детали одного патча по wiki-версии или версии hs-manacost.ru. |
| `GET` | `/api/bg/compositions/screenshot/latest` | Metadata последнего Firecrawl screenshot страницы BG compositions. |
| `GET` | `/api/bg/compositions/screenshot/latest/image` | Файл последнего screenshot BG compositions. |
| `GET` | `/api/db/archetypes` | SQL-backed список последних HSReplay archetype snapshots. |
| `GET` | `/api/db/archetypes/{id}` | Детали архетипа: summary, mulligan, matchups, decks, history. |
| `GET` | `/api/db/archetypes/{id}/mulligan` | Mulligan guide архетипа. |
| `GET` | `/api/db/archetypes/{id}/matchups` | Матчапы архетипа. |
| `GET` | `/api/db/archetypes/{id}/decks` | Сборки архетипа, опционально с картами. |
| `GET` | `/api/db/archetypes/{id}/history` | Popularity/winrate time series. |

## API v1

Версионированные endpoints добавлены независимо от legacy API. Старые `/datasets/*` и `/api/*` не редиректятся и сохраняют прежнюю форму JSON.

| Method | Path | Data |
| --- | --- | --- |
| `GET` | `/v1/constructed/decks` | SQL-backed колоды с фильтрами legacy endpoint. |
| `GET` | `/v1/constructed/archetypes` | Последние успешные snapshots архетипов. |
| `GET` | `/v1/bg/heroes` | Solo/duos герои с пагинацией. |
| `GET` | `/v1/bg/minions` | Последний успешный snapshot существ. |
| `GET` | `/v1/arena/classes` | Классы арены из выбранного кешированного источника. |
| `GET` | `/v1/system/sources` | Типизированный каталог источников. |
| `GET` | `/v1/system/datasets` | Состояние кешей всех источников. |
| `GET` | `/v1/system/health` | Диагностика в v1-конверте; не кешируется. |

Все v1-ответы используют конверт:

```json
{
  "data": [],
  "meta": {
    "source_id": "hsreplay_archetypes",
    "fetched_at": "2026-07-12T08:00:00+00:00",
    "stale": false,
    "count": 42,
    "limit": 100,
    "offset": 0
  }
}
```

Публичные `GET /v1/*`, `GET /api/*` и `GET /datasets*` возвращают:

```http
Cache-Control: public, max-age=300, stale-while-revalidate=600
ETag: "..."
```

`ETag` учитывает путь, query string и время актуального snapshot/dataset. Условный запрос с `If-None-Match` возвращает `304` без тела. `/health`, `/v1/system/health`, `/ops`, `/admin` и `/ui` исключены из публичного кеша.

### `GET /health`

Публичный liveness endpoint. Он специально минимальный: подробные diagnostics перенесены в `/ops/health`.

```json
{
  "ok": true,
  "serving_ok": true,
  "degraded": false,
  "checked_at": "2026-06-07T21:34:41.682806+00:00"
}
```

### `GET /sources`

Query parameters:

| Parameter | Type | Описание |
| --- | --- | --- |
| `site` | string | Фильтр по сайту: `hsreplay`, `hsguru`, `firestone`, `metastats`, `vicious-syndicate`, `hearthstone-decks`, `heartharena`. |
| `category` | string | Фильтр по категории: `ranked`, `meta`, `matchups`, `arena`, `battlegrounds`, `streamer_decks`. |

Пример:

```bash
curl -s "https://api.hs-manacost.ru/sources?site=hsreplay" | jq .
```

Ответ:

```json
{
  "sources": [
    {
      "id": "hsreplay_meta_archetypes_legend_eu_1d",
      "site": "hsreplay",
      "category": "ranked",
      "url": "https://hsreplay.net/meta/#rankRange=LEGEND&tab=archetypes&region=REGION_EU&timeFrame=LAST_1_DAY&popularitySortBy=rank51",
      "fetch_url": "https://hsreplay.net/meta/",
      "fragment": "rankRange=LEGEND&tab=archetypes&region=REGION_EU&timeFrame=LAST_1_DAY&popularitySortBy=rank51",
      "description": "HSReplay meta archetypes grouped by class, Legend EU, last 1 day.",
      "status": {
        "state": "ok",
        "fetched_at": "2026-06-07T21:31:29.446191+00:00",
        "backend": "hsreplay_meta_api"
      },
      "has_dataset": true,
      "dataset_fetched_at": "2026-06-07T21:31:29.446191+00:00"
    }
  ]
}
```

### `GET /datasets/{source_id}`

Основной endpoint потребления данных. Верхний уровень содержит состояние последнего успешного refresh; данные лежат в `data.structured`.

```json
{
  "state": "ok",
  "fetched_at": "2026-06-07T21:33:28.080730+00:00",
  "http_status": 200,
  "final_url": "https://hsreplay.net/cards/#rankRange=GOLD&sortBy=includedPopularity&timeRange=LAST_14_DAYS",
  "content_length": 514780,
  "backend": "hsreplay_cards_api",
  "data": {
    "source_id": "hsreplay_cards_legend_included_popularity",
    "site": "hsreplay",
    "category": "ranked",
    "title": "HSReplay cards, Gold rank, 14 days, sorted by included popularity.",
    "structured": {
      "type": "card_stats",
      "cards": []
    },
    "schema_validation": {
      "ok": true,
      "type": "card_stats",
      "validated": true
    },
    "counts": {
      "tables": 0,
      "json_scripts": 0,
      "deck_codes": 0,
      "links": 0,
      "text_lines": 0,
      "api_bytes": 514780
    }
  }
}
```

Если live refresh упал, но старый cache рабочий, endpoint может продолжать отдавать старый dataset. В status тогда появляются `serving_cached_dataset`, `effective_state=ok_cached`, `last_refresh_state`, `last_refresh_error`, `cached_dataset_age_hours`. Это не ломает публичный `/health`, но видно в `/ops/health`, `/ops/summary` и `python -m app.cli freshness-check`.

Для источников, которые временно не публикуют полноценные upstream-данные,
structured/status diagnostics содержат `upstream_state`. Например,
`vicious_syndicate_live_beta` использует `upstream_unclassified`, пока Firebase
после выхода дополнения содержит только агрегаты `Other <Class>`; такие строки
не выдаются как реальные архетипы и кандидат не проходит publish-gate. Radar
использует `upstream_stale`, если последний доступный полноценный radar issue
отстаёт от последнего Data Reaper report. Такой radar публикуется вместе с
`issue`, `latest_report_issue`, `latest_report_published_at` и предупреждением;
пустой или повреждённый граф по-прежнему блокируется contract-gate.

Опциональная сессия Vicious Syndicate хранится отдельно от HSReplay и
используется только при запросах к домену `vicioussyndicate.com`:

```bash
python -m app.cli vicious-import-storage /secure/path/vicious-cookies.json
python -m app.cli refresh --source vicious_syndicate_radars
python -m app.cli refresh --source vicious_syndicate_live_beta
```

Файл сохраняется с правами `0600` по пути
`VICIOUS_SYNDICATE_STORAGE_PATH` (по умолчанию
`/var/lib/hs-data-api/vicious-syndicate-auth.json`). Cookies помогают пройти
сессионную защиту страниц, но не превращают устаревшие или ещё не
классифицированные upstream-данные в валидный dataset.

Отдельный Docker timer проверяет Vicious каждые два часа:

```bash
systemctl list-timers hs-data-api-docker-refresh-vicious-syndicate.timer
systemctl start hs-data-api-docker-refresh-vicious-syndicate.service
```

### `GET /api/bg/trinkets`

Публичный endpoint для `bg.kolodahearthstone.ru`: объединяет `hsreplay_battlegrounds_trinkets_lesser` и `hsreplay_battlegrounds_trinkets_greater` и сохраняет варианты одной карты по расе.

Query parameters:

| Parameter | Type | Описание |
| --- | --- | --- |
| `trinket_tier` | `all`, `lesser`, `greater` | Фильтр по малым/большим аксессуарам. По умолчанию `all`. |
| `active_only` | boolean | Показывать только строки с `pick_rate` или `avg_placement`. По умолчанию `true`. |

Важные поля строки:

| Field | Описание |
| --- | --- |
| `trinket_tier` / `type` | Lesser или Greater. |
| `tier` | HSReplay tier-группа (`S`, `A`, `B`...), если есть в странице. |
| `cost` | Число на медальоне аксессуара. |
| `tribe`, `race`, `tribe_ru` | Вариант расы для карт вроде `Colorful Compass`. |
| `variant_key` | Стабильный ключ варианта: не дедупить Compass только по `name`. |

Пример:

```bash
curl -s "https://api.hs-manacost.ru/api/bg/trinkets?trinket_tier=lesser" | jq '.trinkets[] | select(.name=="Colorful Compass")'
```

### `GET /datasets/hsreplay_battlegrounds_comps`

HSReplay BG strategies парсятся через Firecrawl: список стратегий берется с `/battlegrounds/comps/`, затем каждая detail-страница обогащает карточку стратегии.

Ключевые поля `data.structured.comps[]`:

| Field | Описание |
| --- | --- |
| `tier` | HSReplay tier стратегии (`S`, `A`, `B`...). |
| `name` | Семейство стратегии, например `Mechs`. |
| `title` / `strategy_title` | Полное название, например `Mechs - Magnetics`. |
| `difficulty` | Сложность HSReplay: `Easy`, `Medium`, `Hard`. |
| `main_cards` / `core_cards` | Ключевые карты стратегии. |
| `additional_cards` / `addon_cards` | Дополнительные синергичные карты. |
| `when_to_commit_cards` | Карты из блока `When to Commit`; использовать как “когда выходить в стратегию”. |
| `enabler_cards` | Карты из блока `Common Enablers`. |
| `how_to_play_cards` | Карты, упомянутые в гайде `How to Play`. |

Пример:

```bash
curl -s "https://api.hs-manacost.ru/datasets/hsreplay_battlegrounds_comps" \
  | jq '.data.structured.comps[] | {tier, title, difficulty, core: [.main_cards[].name], when: [.when_to_commit_cards[].name]}'
```

## HSReplay Archetype Database

Полная архитектура и эксплуатация описаны в
[`docs/HSREPLAY_ARCHETYPE_DATABASE.md`](HSREPLAY_ARCHETYPE_DATABASE.md).

### `GET /api/db/archetypes`

Возвращает последние успешные snapshots по каждому Standard архетипу.

Query parameters:

| Parameter | Default | Описание |
| --- | --- | --- |
| `class_name` | empty | HSReplay class key, например `ROGUE`, `PALADIN`, `DEATHKNIGHT`. |
| `q` | empty | Поиск по имени, slug или exact `archetype_id`. |
| `rank_range` | `LEGEND` | Rank filter. |
| `game_type` | `RANKED_STANDARD` | Game type. |
| `limit` | `100` | 1..500. |
| `offset` | `0` | Offset для pagination. |

Пример:

```bash
curl -s "https://api.hs-manacost.ru/api/db/archetypes?class_name=ROGUE" | jq .
```

### `GET /api/db/archetypes/{id}`

Пример для Herald Rogue:

```bash
curl -s "https://api.hs-manacost.ru/api/db/archetypes/856" | jq .
```

Ответ содержит:

- `snapshot`: summary, фильтры, `as_of_*`, total games, winrate, popularity.
- `mulligan`: display mulligan guide (`rank <= 40`, технические token dbf ids исключены).
- `matchups`: все matchup строки.
- `decks`: популярные сборки без раскрытых карт.
- `history`: popularity/winrate over time.

### `GET /api/db/archetypes/{id}/mulligan`

```bash
curl -s "https://api.hs-manacost.ru/api/db/archetypes/856/mulligan?limit=40" | jq .
```

`display_only=true` включён по умолчанию и соответствует тому, что показывает
вкладка HSReplay Mulligan Guide. Для сырого списка всех карт архетипа используйте
`display_only=false`.

### `GET /api/db/archetypes/{id}/matchups`

```bash
curl -s "https://api.hs-manacost.ru/api/db/archetypes/856/matchups?min_games=100&limit=20" | jq .
```

### `GET /api/db/archetypes/{id}/decks`

```bash
curl -s "https://api.hs-manacost.ru/api/db/archetypes/856/decks?include_cards=true&limit=5" | jq .
```

`include_cards=true` раскрывает карты каждой сборки из `archetype_deck_cards`.

## HSReplay Battlegrounds Minion Database

`refresh-bg-minions-db` сохраняет все BG существа HSReplay в SQLite: карточку
существа, последний snapshot метрик, combat-round breakdown и историю между
запусками. Плановый systemd timer запускается по понедельникам и четвергам.

### `GET /api/db/bg/minions`

Возвращает последние snapshots по каждому BG существу.

Query parameters:

| Parameter | Default | Описание |
| --- | --- | --- |
| `q` | empty | Поиск по английскому/русскому имени или card id. |
| `tavern_tier` | empty | Фильтр таверны 1..7. |
| `limit` | `100` | 1..500. |
| `offset` | `0` | Offset для pagination. |

Пример:

```bash
curl -s "https://api.hs-manacost.ru/api/db/bg/minions?limit=20" | jq .
```

### `GET /api/db/bg/minions/{dbfId}`

Детали одного существа: latest snapshot, raw HSReplay row и `rounds` для
графиков impact/combat winrate/popularity по combat round.

```bash
curl -s "https://api.hs-manacost.ru/api/db/bg/minions/98592" | jq .
```

### `GET /api/db/bg/minions/{dbfId}/history`

История между refresh runs. `chart_series` уже подготовлен в формате
`{x: fetched_at, y: value}` для frontend-графиков.

```bash
curl -s "https://api.hs-manacost.ru/api/db/bg/minions/98592/history" | jq .
```

## HSReplay Battlegrounds Hero Details

`refresh-bg-hero-details` сохраняет HSReplay BG solo tier list, подробные solo
графики по каждому герою и отдельный duos tier list. Для solo подтягиваются
данные "когда улучшать таверну", "когда прожимать силу героя", combat winrate,
composition stats, canonical lineups и final-form minions. Duos намеренно
хранится только как тир-лист: лучший состав для duos не запрашивается.

Если новый dataset еще не собран или временно недоступен, endpoints используют
старый `hsreplay_battlegrounds_heroes` cache как fallback для базового списка
solo-героев. Автоматическое обновление выполняет systemd timer
`hs-data-api-docker-refresh-bg-hero-details.timer` дважды в неделю — по
понедельникам и четвергам в 04:35 Europe/Warsaw. Максимальный штатный разрыв
составляет 96 часов, stale-limit — 120 часов.

### `GET /api/bg/heroes`

Query parameters:

| Parameter | Default | Описание |
| --- | --- | --- |
| `mode` | `solo` | `solo` или `duos`. |
| `q` | empty | Поиск по имени героя. |

```bash
curl -s "https://api.hs-manacost.ru/api/bg/heroes?mode=solo" | jq .
curl -s "https://api.hs-manacost.ru/api/bg/heroes?mode=duos" | jq .
```

### `GET /api/bg/heroes/{dbfId}`

Возвращает весь detail payload solo-героя:

```bash
curl -s "https://api.hs-manacost.ru/api/bg/heroes/57946" | jq .
```

Узкие endpoints для фронтенда и внешних графиков:

```bash
curl -s "https://api.hs-manacost.ru/api/bg/heroes/57946/tavern-up" | jq .
curl -s "https://api.hs-manacost.ru/api/bg/heroes/57946/hero-power" | jq .
curl -s "https://api.hs-manacost.ru/api/bg/heroes/57946/best-composition" | jq .
```

## Hearthstone Patch Database

`scripts/seed_hs_manacost_patches.py --all` берет свежие версии и метаданные из
официальной ленты патчей Blizzard, дополняет историю версиями из
`https://hearthstone.wiki.gg/wiki/Patches`, затем ищет соответствующие
публикации в sitemap/WP API hs-manacost.ru и сохраняет результат в SQLite.
Поэтому задержка обновления Wiki не скрывает новый официальный патч. Полные
wiki-версии с build-номером (например, `35.6.2.245096`) объединяются с
официальными данными без дубликатов, а для Manacost отдельно сохраняется
короткая версия `35.6.2`. Поля `official_url`, `official_title`,
`official_published_at`, `official_modified_at` и `official_summary` явно
показывают первичный источник. Если статья hs-manacost.ru не найдена, строка
все равно сохраняется как `match_state = "missing_manacost"`.

Временные timeout, `408`/`425`/`429` и `5xx` повторяются ограниченно. Если после
повторов не загрузилась одна статья, остальные версии продолжают обновляться,
а прежняя строка проблемной версии не перезаписывается. Такой запуск явно
завершается как `partial` (код `10`), поэтому частичный результат нельзя принять
за полностью свежий каталог. Три последовательных временных сбоя WP API
открывают circuit breaker, а весь запуск ограничен 30 минутами приложения и
35 минутами systemd. Необработанные версии перечисляются в
`not_attempted_versions`; при любом частичном результате старые строки не
удаляются. URL дочерних sitemap принимаются только с точного HTTPS-домена
`hs-manacost.ru`, и это ограничение повторно проверяется на каждом redirect.
Если sitemap перестал показывать ранее найденную статью, полная запись не
понижается до `missing_manacost`: она сохраняется, а запуск отмечается
`partial` с причиной `PreviouslyMatchedArticleMissing`.

Автоматическое обновление выполняет systemd timer:
`hs-data-api-docker-refresh-patches.timer`.

### `GET /api/patches`

Query parameters:

| Parameter | Default | Описание |
| --- | --- | --- |
| `q` | empty | Поиск по wiki-версии, версии Manacost, заголовку и summary. |
| `match_state` | empty | Фильтр `matched` или `missing_manacost`. |
| `include_content` | `false` | Включить `content_text` в записи списка. |
| `limit` | `20` | 1..500. |
| `offset` | `0` | Offset для pagination. |

```bash
curl -s "https://api.hs-manacost.ru/api/patches?limit=2" | jq .
```

### `GET /api/patches/{version}`

`version` принимает полную wiki-версию (`35.6.2.245096`) и короткую версию
Manacost (`35.6.2`). По умолчанию detail включает `content_text`; можно
отключить:

```bash
curl -s "https://api.hs-manacost.ru/api/patches/35.6.2?include_content=false" | jq .
```

## HSReplay Battlegrounds Compositions Screenshot

`capture-bg-compositions-screenshot` делает Firecrawl screenshot страницы
`https://hsreplay.net/battlegrounds/compositions/`, сохраняет файл локально в
`data/firecrawl/screenshots/hsreplay_battlegrounds_compositions/` и обновляет
`latest.json`. Плановый systemd timer запускается ежедневно.

```bash
curl -s "https://api.hs-manacost.ru/api/bg/compositions/screenshot/latest" | jq .
curl -L "https://api.hs-manacost.ru/api/bg/compositions/screenshot/latest/image" -o bg-compositions.png
```

## Admin And Ops Endpoints

Все endpoints из этого раздела требуют `X-API-Key`.

| Method | Path | Назначение |
| --- | --- | --- |
| `POST` | `/admin/refresh` | Запустить refresh одного или нескольких источников. |
| `POST` | `/admin/refresh/hsreplay-archetypes` | Запустить обновление SQLite archetype snapshots. |
| `POST` | `/admin/refresh/bg-minions-db` | Запустить обновление SQLite BG minion snapshots. |
| `POST` | `/admin/refresh/bg-hero-details` | Запустить обновление BG hero details и duos tier list. |
| `POST` | `/admin/capture/bg-compositions-screenshot` | Сделать Firecrawl screenshot BG compositions. |
| `PUT` | `/admin/datasets/{source_id}` | Ручная загрузка JSON dataset в cache. |
| `GET` | `/ops/health` | Подробное состояние источников: states, stale, cached, semantic quality, data dir. |
| `GET` | `/health/premium` | Проверка локального premium auth состояния. |
| `GET` | `/health/premium?live=true` | Live-probe HSReplay/VS premium endpoints. |
| `GET` | `/ops/summary` | Сводка событий refresh за период. |
| `GET` | `/ops/events` | Журнал событий refresh с фильтрами. |
| `GET` | `/ops/trace/{trace_id}` | Timeline одного source trace. |
| `GET` | `/ops/run/{run_id}` | Timeline одного refresh run. |

### `POST /admin/refresh`

Запустить refresh всех источников:

```bash
curl -s -X POST \
  -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/admin/refresh" | jq .
```

Запустить один или несколько источников:

```bash
curl -s -X POST \
  -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/admin/refresh?source_id=hsreplay_meta_archetypes_legend_eu_1d&source_id=vicious_syndicate_live_beta" | jq .
```

### `GET /ops/health`

Подробная диагностика cache/refresh. В отличие от `/health`, endpoint закрыт admin key.

```json
{
  "ok": true,
  "serving_ok": true,
  "freshness_ok": true,
  "degraded": false,
  "data_dir": "/var/lib/hs-data-api",
  "sources": 46,
  "states": {
    "ok": 46
  },
  "hard_failed_sources": [],
  "semantic_failed_sources": [],
  "semantic_failures": [],
  "cached_sources": [],
  "cached_after_failure_sources": [],
  "stale_sources": [],
  "stale_count": 0,
  "cached_count": 0,
  "cached_after_failure_count": 0
}
```

`serving_ok=false` выставляется не только при transport/refresh error, но и когда
уже сохранённый dataset не проходит contract или семантическую проверку (например, все
архетипы являются `Other <Class>` или radar относится к старому отчёту). В
`GET /sources/{source_id}` тот же объединённый результат доступен в поле
`semantic_quality`; подробный contract report находится во вложенном `contract`.

### `GET /health/premium`

Локальная проверка premium auth без сетевого live-probe.

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/health/premium" | jq .
```

Live probe:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/health/premium?live=true" | jq .
```

Проверяет:

- HSReplay saved session и premium-readable endpoint.
- Vicious Syndicate premium Firebase data availability.

Ответ не возвращает cookie values, токены, session id и Firebase auth token.

### `GET /ops/summary`

Query parameters:

| Parameter | Type | Default | Описание |
| --- | --- | --- | --- |
| `since_hours` | float | `24.0` | Окно анализа от `1` до `168` часов. |

Пример:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/ops/summary?since_hours=48" | jq .
```

### `GET /ops/events`

Query parameters:

| Parameter | Type | Описание |
| --- | --- | --- |
| `limit` | int | 1-2000 events. |
| `source_id` | string | Фильтр по source id. |
| `event` | string | Фильтр по типу event. |
| `action` | string | Фильтр по action. |
| `action_group` | string | Фильтр по группе action. |
| `level` | string | `info`, `warn`, `error`. |
| `trace_id` | string | Фильтр по trace. |
| `run_id` | string | Фильтр по refresh run. |
| `since_hours` | float | Окно анализа. |

Пример:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/ops/events?since_hours=48&level=error&limit=20" | jq .
```

## SQL-backed Endpoints

### `GET /api/db/decks`

Query parameters:

| Parameter | Type | Описание |
| --- | --- | --- |
| `class_name` | string | Фильтр по классу. |
| `format_name` | string | `Standard`, `Wild`, etc. |
| `source_id` | string | Фильтр по источнику. |
| `min_win_rate` | float | Минимальный win rate. |
| `q` | string | Поиск по title/archetype/deck_code. |
| `limit` | int | По умолчанию 50. |
| `offset` | int | Offset pagination. |

### `GET /api/db/cards/trends`

Query parameters:

| Parameter | Type | Required | Описание |
| --- | --- | --- | --- |
| `card_name` | string | yes | Название карты. |
| `source_id` | string | no | Фильтр по source id. |
| `class_name` | string | no | Фильтр по классу. |
| `limit` | int | no | По умолчанию 100. |

## Source IDs

### HSGuru

- `hsguru_streamer_decks_legend_1000`
- `hsguru_meta_standard_legend`
- `hsguru_meta_standard_diamond_4to1`
- `hsguru_meta_standard_top_5k`
- `hsguru_meta_standard_top_legend`
- `hsguru_meta_wild_legend`
- `hsguru_meta_wild_diamond_4to1`
- `hsguru_meta_wild_top_legend`
- `hsguru_meta_wild_top_5k`
- `hsguru_matchups_legend`
- `hsguru_matchups_wild_legend`
- `hsguru_matchups_diamond_4to1`

### HSReplay

- `hsreplay_battlegrounds_comps`
- `hsreplay_battlegrounds_heroes`
- `hsreplay_battlegrounds_minions`
- `hsreplay_battlegrounds_compositions`
- `hsreplay_battlegrounds_trinkets_lesser`
- `hsreplay_battlegrounds_trinkets_greater`
- `hsreplay_arena`
- `hsreplay_arena_legendaries`
- `hsreplay_arena_winning_decks`
- `hsreplay_arena_cards_advanced`
- `hsreplay_decks_trending`
- `hsreplay_cards_legend_included_winrate`
- `hsreplay_cards_legend_included_popularity`
- `hsreplay_cards_legend_1d`
- `hsreplay_cards_wild_legend_1d`
- `hsreplay_meta_archetypes_legend_eu_1d`
- `hsreplay_meta_top_1000_legend_1d_firecrawl`
- `hsreplay_meta_legend_1d_firecrawl`
- `hsreplay_meta_diamond_4to1_1d_firecrawl`
- `hsreplay_arena_class_pages_firecrawl`

### Firestone

- `firestone_battlegrounds_comps`
- `firestone_battlegrounds_cards`
- `firestone_battlegrounds_spells`
- `firestone_arena_cards_normal`
- `firestone_arena_cards_underground`
- `firestone_arena_legendaries_underground`
- `firestone_arena_legendaries_normal`

### Other Sources

- `heartharena_tierlist`
- `metastats_decks`
- `metastats_matchups`
- `hearthstone_decks`
- `vicious_syndicate_radars`
- `vicious_syndicate_live_beta`

## Structured Data Types

`data.structured.type` определяет схему payload.

| Type | Основные поля | Источники |
| --- | --- | --- |
| `card_stats` | `cards[]` with `id`, `dbfId`, `deck_popularity`, `copies`, `deck_winrate`, `games_played`, `wins_when_played`, `kept`, `winrate_when_drawn`, `avg_turns_in_hand`, `avg_turn_played_on` | HSReplay cards |
| `arena_card_tiers` | `cards[]`, `by_class`, `total_cards`, `primary_class` | HSReplay Arena advanced |
| `arena_class_pages` | `classes[]` with `class`, `slug`, `win_rate`, `pct_7_plus`, `pick_rate`, `num_drafts`, per-class Firecrawl status | HSReplay Arena class pages |
| `bg_heroes` | `heroes[]` with `hero`, `dbfId`, `pick_rate`, `best_comp`, `avg_placement`, `tier`, `placement_distribution` | HSReplay BG heroes |
| `bg_minions` | `minions[]` with `minion`, `minion_dbf_id`, `impact`, `win_share`, `popularity` | HSReplay BG minions |
| `bg_compositions` | `compositions[]` with `type`, `first_place`, `avg_placement`, `popularity`, `placement_distribution` | HSReplay BG compositions |
| `hsreplay_meta_archetypes` | `classes[]` grouped by class, each with `archetypes[]` | HSReplay meta archetypes |
| `vicious_live` | `class_distribution`, `deck_distribution`, `tier_list` | VS Data Reaper Live |
| `vicious_syndicate_radars` | `classes_summary`, `radars[]`, `nodes[]`, `edges[]` | VS radars |
| `metastats_decks` | `decks[]` with archetype/deck/card details | MetaStats decks |
| `metastats_matchups` | `matchups[]`, `archetypes[]` | MetaStats matchups |
| `hearthstone_decks` | `decks[]`, `standard_count`, `wild_count` | Hearthstone-Decks |
| `bg_card_stats` | `tiers` keyed by tavern tier | Firestone BG cards/spells |

Structured datasets created by API-first parsers include:

```json
{
  "schema_validation": {
    "ok": true,
    "type": "card_stats",
    "validated": true
  }
}
```

If a legacy/generic dataset has no registered schema, `validated` can be `false` with `reason: "no schema registered"`.

## Examples

HSReplay cards, Legend, last 1 day:

```bash
curl -s "https://api.hs-manacost.ru/datasets/hsreplay_cards_legend_1d" \
  | jq '.data.structured.cards[0:5]'
```

HSReplay Wild cards, Legend, last 1 day:

```bash
curl -s "https://api.hs-manacost.ru/datasets/hsreplay_cards_wild_legend_1d" \
  | jq '.data.structured.cards[0:5]'
```

HSReplay meta archetypes grouped by class:

```bash
curl -s "https://api.hs-manacost.ru/datasets/hsreplay_meta_archetypes_legend_eu_1d" \
  | jq '.data.structured.classes[] | {class, winrate, popularity, games, archetypes: .archetypes[0:3]}'
```

HSReplay Battlegrounds heroes:

```bash
curl -s "https://api.hs-manacost.ru/datasets/hsreplay_battlegrounds_heroes" \
  | jq '.data.structured.heroes[0:5]'
```

Vicious Syndicate Live tier list:

```bash
curl -s "https://api.hs-manacost.ru/datasets/vicious_syndicate_live_beta" \
  | jq '.data.structured.tier_list'
```

Detailed source diagnostics:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/ops/health" | jq .
```

Premium auth live probe:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/health/premium?live=true" | jq .
```
