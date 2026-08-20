# Koloda Hearthstone API

Актуальная документация REST API для `api.kolodahearthstone.com`.

Практический каталог наборов, полей и готовых запросов:
[DATA_CATALOG.md](DATA_CATALOG.md).

Production base URL:

```text
https://api.kolodahearthstone.com
```

Локально:

```text
http://127.0.0.1:8000
```

## Auth

Публичные read-only endpoints доступны без ключа. Закрытые endpoints используют
scoped-токены:

```http
Authorization: Bearer khs_v1_<token-id>_<secret>
```

`X-API-Key` и `HS_API_KEY` временно поддерживаются как bootstrap-совместимость.
Выпуск, scopes и ротация описаны в [API_TOKENS.md](API_TOKENS.md).

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
| `GET` | `/v1/battlegrounds/heroes` | Solo/duos герои с пагинацией. |
| `GET` | `/v1/battlegrounds/minions` | Последний успешный snapshot существ. |
| `GET` | `/v1/arena/classes` | Классы арены из выбранного кешированного источника. |
| `GET` | `/v1/sources` | Типизированный каталог источников. |
| `GET` | `/v1/datasets` | Состояние кешей всех источников. |
| `GET` | `/v1/health` | Диагностика в v1-конверте; не кешируется. |
| `GET` | `/v1/system/parsing-reliability` | Честная надёжность и проверенная полнота за 24h, 7d и 30d; `no-store`. |

Старые `/v1/bg/*` и `/v1/system/{sources,datasets,health}` остаются
deprecated aliases и возвращают тот же JSON.

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

Большинство публичных `GET /v1/*`, `GET /api/*` и `GET /datasets*` возвращают:

```http
Cache-Control: public, max-age=300, stale-while-revalidate=600
ETag: "..."
```

`ETag` учитывает путь, query string и время актуального snapshot/dataset. Условный запрос с `If-None-Match` возвращает `304` без тела. `/health`, `/v1/health`, `/v1/system/health`, `/v1/system/parsing-reliability`, `/ops`, `/admin` и `/ui` исключены из публичного кеша.

### `GET /v1/system/parsing-reliability`

Endpoint возвращает независимые окна `24h`, `7d` и `30d`. LKG означает
доступность старого набора и не попадает в `fresh_published` или
`verified_completeness.complete_fresh`. Ответ всегда имеет
`Cache-Control: no-store`.

Поля `verified_completeness`:

| Поле | Знаменатель и смысл |
| --- | --- |
| `instrumented_sources / catalog_sources` | Охват completeness-телеметрией текущего operational-каталога. Отсутствие строгого retrieval-доказательства даёт `unknown`, а не успех. |
| `observed_instrumented_sources / instrumented_sources` | Сколько инструментированных источников реально наблюдалось в окне. |
| `tracked_attempts / eligible_attempts` | Доля parser-attempts с явным completeness-состоянием; отсутствие строгого evidence сохраняется как `unknown`, включая влияние terminal deficits на знаменатель. |
| `complete_fresh / tracked_attempts` | Взвешенный процент попыток: опубликован новый кандидат, retrieval полный и freshness доказана. |
| `states` | Число `complete`, доказанно `incomplete` и `unknown`; сумма равна `tracked_attempts`. |
| `macro_complete_fresh_rate_pct` | Невзвешенное среднее per-source rates по всем инструментированным источникам; ненаблюдавшийся источник даёт 0. |
| `macro_target_met` | Результат точного сравнения неокруглённого среднего per-source rates с целью 99%; интерфейс не восстанавливает его из округлённого процента. |
| `worst_observed_source_rate_pct` | Худший rate среди реально наблюдавшихся инструментированных источников. |
| `sources_meeting_target / instrumented_sources` | Доля источников, каждый из которых отдельно выполняет цель 99%. |
| `objective_status` | `met` только при полном измеряемом окне и прохождении всех coverage/rate gates; иначе `collecting` или `miss`. |

Поля `scheduled_reliability`:

| Поле | Смысл |
| --- | --- |
| `schedule_coverage_ratio` | Доля primary-расписаний, уже подключённых к durable ledger. |
| `temporal_coverage_ratio` | Доля выбранного окна между `coverage_started_at` и непрерывным `materialized_through`. |
| `due_slots` | Eligible-слоты, дедлайн которых уже наступил. |
| `on_time_fresh` | Новая публикация завершена не позже дедлайна. |
| `on_time_nonfresh` | До дедлайна был terminal, но не fresh publication. |
| `late` | Первый пригодный terminal появился только после дедлайна. |
| `missing` | После дедлайна нет ни одного non-skipped terminal. |
| `excluded_slots` | Сохранённые, но не включённые в знаменатель решения (`section-disabled` или `operationally-disabled`). |

Граница 99% проверяется по целым счётчикам до округления. Ledger охватывает все
15 primary-расписаний и обнаруживает полностью не стартовавшие запуски;
условный post-patch timer вне активного окна материализуется как исключённый.
Пока выбранное временное окно не накоплено полностью, `ledger_status=partial`,
а его `measurement_status` и `objective_status` остаются `collecting`.

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
curl -s "https://api.kolodahearthstone.com/sources?site=hsreplay" | jq .
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
не выдаются как реальные архетипы и кандидат не проходит publish-gate. Последний
полный Radar использует `upstream_stale`, если его issue отстаёт от последнего
Data Reaper report. Во время незавершённой публикации новый кандидат и status
получают `upstream_publication_pending`, а прежний полный snapshot отдаётся
только как явный LKG (`serving_cached_dataset=true`,
`fresh_candidate_published=false`). Известные официальные radar URL сначала
проверяются дешёвым direct-readiness запросом; платные fallback-провайдеры для
подтверждённо отсутствующего upstream-файла не вызываются. Пустой, повреждённый
или смешанный граф по-прежнему блокируется contract-gate.

#### `GET /datasets/hearthstone_decks`

Возвращает 20 Standard и 20 Wild Legend-постов. Основной маршрут выполняет
ровно два прямых WordPress REST GET для категорий `3` и `13`. Поля включают
`wordpress_post_id`, `published_at`, `modified_at`, `title`, `url`, `format`,
`archetype`, `rank`, `player`, `score`, `deck_code`, `deck_code_status` и
provenance. REST-кандидат проходит проверку структуры и декодирование deck
codes. Для отсутствующего кода разрешены LKG и точечный detail fetch; при
отказе REST целиком используется валидированный HTML fallback.

Верхнеуровневые поля `data.structured` показывают `fetch_strategy`,
`wordpress_rest_requests`, `html_list_pages`, `cached_deck_codes_reused`,
`deck_code_fill_rate`, `standard_count` и `wild_count`. К публикации допускаются
ровно 40 строк и не менее 95% заполненных deck codes; иначе продолжает
обслуживаться предыдущий LKG.

#### `GET /datasets/firestone_standard`

Возвращает два массива Firestone Standard Legend за `last-patch`:
`decks[]` и `archetypes[]`. Парсер параллельно делает два прямых CDN GET к
ZeroToHeroes и не использует Scrape.do, Firecrawl, Bright Data, Scrapfly или
residential proxy. Общие поля строки: `archetype_id`, `archetype_name`,
`player_class`, `games`, `wins`, `winrate`, `core_cards`, `hero_card_ids`;
колода также содержит `decklist`/`deck_code` и `card_variations`. `winrate` —
доля в диапазоне `0..1`, не процент `0..100`.

Контракт требует минимум 20 строк суммарно, минимум 10 колод и 10 архетипов,
оба metadata-блока и прохождение schema, semantic и regression gates.
`last_updated` каждого среза должен быть timezone-aware ISO не старше 36 часов
и не более чем на 6 часов впереди серверного времени; регрессия числа строк
считается отдельно для `decks` и `archetypes`. Если один CDN-срез невалиден или
контракт не выполнен, новый snapshot не публикуется и endpoint продолжает
отдавать LKG.

```bash
# Запускайте отдельно внутри штатного production-контейнера.
docker exec hs-data-api python -m app.cli refresh \
  --source hearthstone_decks --require-all-ok
docker exec -e HS_FIRESTONE_STANDARD_AUTHORIZED=true hs-data-api \
  python -m app.cli refresh \
  --source firestone_standard --require-all-ok
```

> `firestone_standard` нельзя включать в публичном или коммерческом production
> без письменного разрешения Firestone/ZeroToHeroes. См.
> [Firestone Terms of Service](https://github.com/Zero-to-Heroes/firestone/blob/master/tos.md).
> По умолчанию `HS_FIRESTONE_STANDARD_AUTHORIZED=false`: сетевой fetch и
> scheduled refresh заблокированы, старый кэш не выдаётся через dataset/demo/v1,
> а источник не входит в denominator verified completeness. Флаг `true` —
> явное подтверждение оператора, что необходимое разрешение получено.

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
curl -s "https://api.kolodahearthstone.com/api/bg/trinkets?trinket_tier=lesser" | jq '.trinkets[] | select(.name=="Colorful Compass")'
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
curl -s "https://api.kolodahearthstone.com/datasets/hsreplay_battlegrounds_comps" \
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
curl -s "https://api.kolodahearthstone.com/api/db/archetypes?class_name=ROGUE" | jq .
```

### `GET /api/db/archetypes/{id}`

Пример для Herald Rogue:

```bash
curl -s "https://api.kolodahearthstone.com/api/db/archetypes/856" | jq .
```

Ответ содержит:

- `snapshot`: summary, фильтры, `as_of_*`, total games, winrate, popularity.
- `mulligan`: display mulligan guide (`rank <= 40`, технические token dbf ids исключены).
- `matchups`: все matchup строки.
- `decks`: популярные сборки без раскрытых карт.
- `history`: popularity/winrate over time.

### `GET /api/db/archetypes/{id}/mulligan`

```bash
curl -s "https://api.kolodahearthstone.com/api/db/archetypes/856/mulligan?limit=40" | jq .
```

`display_only=true` включён по умолчанию и соответствует тому, что показывает
вкладка HSReplay Mulligan Guide. Для сырого списка всех карт архетипа используйте
`display_only=false`.

### `GET /api/db/archetypes/{id}/matchups`

```bash
curl -s "https://api.kolodahearthstone.com/api/db/archetypes/856/matchups?min_games=100&limit=20" | jq .
```

### `GET /api/db/archetypes/{id}/decks`

```bash
curl -s "https://api.kolodahearthstone.com/api/db/archetypes/856/decks?include_cards=true&limit=5" | jq .
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
curl -s "https://api.kolodahearthstone.com/api/db/bg/minions?limit=20" | jq .
```

### `GET /api/db/bg/minions/{dbfId}`

Детали одного существа: latest snapshot, raw HSReplay row и `rounds` для
графиков impact/combat winrate/popularity по combat round.

```bash
curl -s "https://api.kolodahearthstone.com/api/db/bg/minions/98592" | jq .
```

### `GET /api/db/bg/minions/{dbfId}/history`

История между refresh runs. `chart_series` уже подготовлен в формате
`{x: fetched_at, y: value}` для frontend-графиков.

```bash
curl -s "https://api.kolodahearthstone.com/api/db/bg/minions/98592/history" | jq .
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
curl -s "https://api.kolodahearthstone.com/api/bg/heroes?mode=solo" | jq .
curl -s "https://api.kolodahearthstone.com/api/bg/heroes?mode=duos" | jq .
```

### `GET /api/bg/heroes/{dbfId}`

Возвращает весь detail payload solo-героя:

```bash
curl -s "https://api.kolodahearthstone.com/api/bg/heroes/57946" | jq .
```

Узкие endpoints для фронтенда и внешних графиков:

```bash
curl -s "https://api.kolodahearthstone.com/api/bg/heroes/57946/tavern-up" | jq .
curl -s "https://api.kolodahearthstone.com/api/bg/heroes/57946/hero-power" | jq .
curl -s "https://api.kolodahearthstone.com/api/bg/heroes/57946/best-composition" | jq .
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
curl -s "https://api.kolodahearthstone.com/api/patches?limit=2" | jq .
```

### `GET /api/patches/{version}`

`version` принимает полную wiki-версию (`35.6.2.245096`) и короткую версию
Manacost (`35.6.2`). По умолчанию detail включает `content_text`; можно
отключить:

```bash
curl -s "https://api.kolodahearthstone.com/api/patches/35.6.2?include_content=false" | jq .
```

## HSReplay Battlegrounds Compositions Screenshot

`capture-bg-compositions-screenshot` делает Firecrawl screenshot страницы
`https://hsreplay.net/battlegrounds/compositions/`, сохраняет файл локально в
`data/firecrawl/screenshots/hsreplay_battlegrounds_compositions/` и обновляет
`latest.json`. Плановый systemd timer запускается ежедневно.

```bash
curl -s "https://api.kolodahearthstone.com/api/bg/compositions/screenshot/latest" | jq .
curl -L "https://api.kolodahearthstone.com/api/bg/compositions/screenshot/latest/image" -o bg-compositions.png
```

## Admin And Ops Endpoints

Все endpoints из этого раздела требуют scope `admin` или временный bootstrap
`HS_API_KEY`.

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
  "https://api.kolodahearthstone.com/admin/refresh" | jq .
```

Запустить один или несколько источников:

```bash
curl -s -X POST \
  -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.kolodahearthstone.com/admin/refresh?source_id=hsreplay_meta_archetypes_legend_eu_1d&source_id=vicious_syndicate_live_beta" | jq .
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
  "sources": 97,
  "states": {
    "ok": 97
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
  "https://api.kolodahearthstone.com/health/premium" | jq .
```

Live probe:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.kolodahearthstone.com/health/premium?live=true" | jq .
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
  "https://api.kolodahearthstone.com/ops/summary?since_hours=48" | jq .
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
  "https://api.kolodahearthstone.com/ops/events?since_hours=48&level=error&limit=20" | jq .
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

- `firestone_standard`
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
| `firestone_standard` | `decks[]`, `archetypes[]`, metadata/totals; `winrate` is `0..1` | Firestone Standard Legend last-patch |
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

### Контракт полноты данных v1

Новые snapshots источников `hsreplay_battlegrounds_minions`,
`hsreplay_arena_cards_advanced`, `hsreplay_arena_legendaries` и
`firestone_standard` публикуют проверяемый контракт:

```json
{
  "completeness_schema_version": 1,
  "population_completeness": "unverifiable",
  "upstream_freshness": {
    "status": "fresh",
    "reason": null,
    "observed_at": "2026-08-14T02:20:00+00:00",
    "age_seconds": 3600,
    "evidence": ["meta_period_id", "selected_params", "last_modified"],
    "meta_period_id": 16,
    "selected_params": [
      "ArenaGameTypeFilter.BGT_UNDERGROUND_ARENA",
      "ArenaTimestampRangeFilter.LAST_4_DAYS"
    ],
    "filters_match": true,
    "response_headers": {}
  },
  "row_retrieval": {
    "raw_rows": 901,
    "eligible_rows": 901,
    "normalized_rows": 900,
    "explained_drops": 0,
    "unexplained_drops": 1,
    "drop_reasons": {
      "explained": {},
      "unexplained": {"normalizer_rejected": 1}
    },
    "scope": "primary_class:ALL"
  },
  "cards": [
    {
      "deck_winrate": null,
      "field_availability": {
        "deck_winrate": {
          "available": false,
          "reason": "no_games_in_window"
        }
      }
    }
  ]
}
```

`row_retrieval` обязан удовлетворять условиям
`raw_rows >= eligible_rows >= normalized_rows` и
`raw_rows - normalized_rows = explained_drops + unexplained_drops`. Суммы в
`drop_reasons` должны совпадать с соответствующими счётчиками. Разрешённый
список причин для `explained` сейчас пуст: произвольное объяснение не позволяет
выдать потерянную строку за успешное получение. Для Arena Cards считается
только выбранный первичный class slice, а для Arena Legendaries — уникальные
package keys, поэтому дубликаты классовых представлений не искажают процент.
Для `hsreplay_arena_legendaries` объект дополнительно содержит
`bucket_coverage`: фиксированный `expected_buckets` (`ALL` и 11 классов),
фактически полученные `observed_buckets`, отсутствующие `missing_buckets`,
неизвестные `unknown_buckets` и `duplicate_bucket_package_keys`. Пустой список
строк внутри ожидаемого bucket допустим, но отсутствие самого ключа, неизвестный
bucket или повтор пары `(bucket, package_key)` делают snapshot неполным. Поэтому
free/Firecrawl-ответ только с `ALL` может использоваться как диагностический
fallback, но не публикуется как новый полностью полученный v1 snapshot.

`population_completeness="unverifiable"` обязателен для трёх HSReplay v1
схем: upstream не публикует total/pagination contract, поэтому API не заявляет
полноту всей популяции. `upstream_freshness.status` отделяет свежесть upstream
представления от времени нашего HTTP-запроса. BG доказывает её body-полем
`as_of` и допускает возраст до 36 часов; Arena требует точные filters,
положительный `meta_period_id` и `Last-Modified` не старше 6 часов. Заголовок
`Date` сохраняется только как контекст и не является доказательством изменения
данных.

`stale`, неверные filters/metadata, malformed/future timestamps и mismatch
body/header блокируют новый snapshot. Отсутствие target headers у fallback
фиксируется как `unknown` (`missing_last_modified` или
`transport_evidence_unavailable`), а не как выдуманная свежесть. Такой
кандидат может пройти остальные content gates, но не считается complete-fresh
в telemetry; LKG продолжает обслуживаться при отклонении кандидата.

Отсутствующее поле засчитывается как полученное только при согласованном
`field_availability.available=false` и детерминированной причине из списка:

| Источник | Поля | Допустимые объяснения отсутствия |
| --- | --- | --- |
| `hsreplay_battlegrounds_minions` | `impact`, `win_share`, `popularity` | `no_current_patch_aggregates`, `insufficient_current_patch_sample` |
| `hsreplay_arena_cards_advanced` | `deck_winrate`, `winrate_when_drawn`, `winrate_when_played` | `no_games_in_window` |
| `hsreplay_arena_legendaries` | `winrate`, включая каждый `by_class` bucket | `upstream_unavailable_at_zero_pick_rate` |
| `hsreplay_arena_legendaries` | `score`, включая каждый `by_class` bucket | `upstream_score_not_reported` |
| `firestone_standard` | `core_cards` | `generic_class_bucket_without_observed_deck_cluster` |

Пустой `core_cards` у Firestone-архетипа, который не является generic class
bucket, получает причину
`empty_core_cards_without_deterministic_explanation`; это необъяснённая потеря
и quality gate её отклоняет.

В v1 числовые значения проверяются до форматирования. Arena rates должны быть
конечными числами в диапазоне `0..100`; `false`, `NaN`, бесконечность,
произвольный текст и значения вне диапазона отвергаются. `score` обязан быть
конечным числом либо `null` с согласованным
`field_availability.score=upstream_score_not_reported`; подстановка нуля вместо
отсутствующего upstream-значения запрещена. `avg_copies` — конечное
неотрицательное число. У Arena Legendaries каждый package обязан иметь
непустой `package_card_ids`, а каждый нормализованный элемент `cards[]` —
непустой `card_id` и положительный целый `count`. Уникальность идентичностей
также входит в retrieval gate: `card_id` для Arena Advanced,
`minion_dbf_id` для BG minions, `(bucket, package_key)` для Arena Legendaries,
отдельно `decklist` и `archetype_id` для Firestone.

Показатели в `status.quality` имеют разные назначения:

- `quality_score` и `metric_availability_score` показывают долю реально
  заполненных критичных метрик;
- `retrieval_completeness_score` показывает честную полноту получения и равен
  минимуму из полноты критичных полей и полноты строк;
- `retrieval_complete=true` возможен только без необъяснённых пропусков,
  конфликтов descriptors и потерь строк. Для Arena Legendaries в расчёт входит
  отдельные показатели `critical_fields["by_class.winrate"]` и
  `critical_fields["by_class.score"]` по всем class buckets.

Порог `min_field_fill_rate` строгой v1-схемы использует retrieval-долю
(`available + allow-listed explained_unavailable`), сохраняя
`metric_availability_score` как сырой процент реально заполненных значений.
Поэтому низкая выборка после патча не маскируется как наличие метрик, но и не
считается ошибкой получения, если причина согласована и входит в allow-list.
Для BG minions дополнительно проверяются placements `1..8`, физический
`impact` в `-7..7`, rates `0..100`, целые неотрицательные counts и
reconciliation placement sums.

Поддерживается только точная версия `completeness_schema_version=1`; явно
переданные `0`, `2` или неизвестная будущая версия отклоняются fail-closed.
Snapshot без `completeness_schema_version` считается legacy: он остаётся
доступным во время постепенного перехода, а `retrieval_completeness_score` и
`retrieval_complete` для него равны `null`, то есть «неизвестно», а не
«успешно». При `effective_state=ok_cached` показатели относятся к фактически
отдаваемому LKG snapshot; диагностика отклонённого свежего кандидата хранится
отдельно в `last_refresh_quality`.

## Examples

HSReplay cards, Legend, last 1 day:

```bash
curl -s "https://api.kolodahearthstone.com/datasets/hsreplay_cards_legend_1d" \
  | jq '.data.structured.cards[0:5]'
```

HSReplay Wild cards, Legend, last 1 day:

```bash
curl -s "https://api.kolodahearthstone.com/datasets/hsreplay_cards_wild_legend_1d" \
  | jq '.data.structured.cards[0:5]'
```

HSReplay meta archetypes grouped by class:

```bash
curl -s "https://api.kolodahearthstone.com/datasets/hsreplay_meta_archetypes_legend_eu_1d" \
  | jq '.data.structured.classes[] | {class, winrate, popularity, games, archetypes: .archetypes[0:3]}'
```

HSReplay Battlegrounds heroes:

```bash
curl -s "https://api.kolodahearthstone.com/datasets/hsreplay_battlegrounds_heroes" \
  | jq '.data.structured.heroes[0:5]'
```

Vicious Syndicate Live tier list:

```bash
curl -s "https://api.kolodahearthstone.com/datasets/vicious_syndicate_live_beta" \
  | jq '.data.structured.tier_list'
```

Detailed source diagnostics:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.kolodahearthstone.com/ops/health" | jq .
```

Premium auth live probe:

```bash
curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.kolodahearthstone.com/health/premium?live=true" | jq .
```
