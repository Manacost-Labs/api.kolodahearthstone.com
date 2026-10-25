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
| `GET` | `/v1/constructed/deck-radar` | Beta: новые точные deckstrings из HSGuru streamer snapshots, отфильтрованные против current HSGuru catalog; `status=candidate|confirmed`. |
| `GET` | `/v1/constructed/balance-radar` | Радар нерфов и баффов (Стандарт/Вольный): статистика + Reddit/X + анализ Claude, последний успешный прогон; `format=standard|wild`. История: `/runs`, `/runs/{run_id}`. Прошлые патчи Blizzard и закономерности: `/history`, `/patterns`. Контракт: [BALANCE_RADAR.md](BALANCE_RADAR.md). |
| `GET` | `/v1/constructed/archetypes` | Последние успешные snapshots архетипов. |
| `GET` | `/v1/battlegrounds/heroes` | Solo/duos герои с пагинацией. |
| `GET` | `/v1/battlegrounds/minions` | Последний успешный snapshot существ. |
| `GET` | `/v1/battlegrounds/minions/stats` | Полная статистика HSReplay по всем существам среза: итоги обычной и золотой версии. |
| `GET` | `/v1/battlegrounds/minions/stats/slices` | Какие срезы MMR/периода сохранены и когда обновлены. |
| `GET` | `/v1/battlegrounds/minions/{minion}/stats` | Страница существа как на HSReplay: по ходам, позиции, покупки, медианы. |
| `GET` | `/v1/battlegrounds/minions/{minion}/stats/history` | История итогов существа по снимкам («как изменилось»). |
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

`full_fresh_rate_pct` измеряет надёжность парсера только для eligible-попыток.
`end_to_end_fresh_rate_pct` дополнительно включает в знаменатель доказанные
`upstream_pending_attempts`: источник ещё не выпустил артефакт, поэтому это не
ошибка парсера, но свежих данных у пользователя всё равно нет.

`outcome_recovery` объясняет исторические `provisional` и `lkg_served`, не
удаляя их из исходного окна и SLO-знаменателя. Для каждого типа сумма
`recovered_to_fresh + reclassified_upstream_pending + unresolved` строго равна
`events` и соответствующему значению в `counts`. Переклассификация upstream
означает только доказанную задержку публикации: обслуживаемые данные всё ещё
были несвежими.

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
| `on_time_upstream_pending` | До дедлайна независимо подтверждено, что upstream ещё не опубликовал нужный артефакт. Не считается `missing` или parser failure. |
| `on_time_nonfresh` | До дедлайна был terminal, но не fresh publication. |
| `late` | Первый пригодный terminal появился только после дедлайна. |
| `missing` | После дедлайна нет ни одного non-skipped terminal. |
| `excluded_slots` | Сохранённые, но не включённые в знаменатель решения (`section-disabled` или `operationally-disabled`). |
| `parser_eligible_due_slots` | `due_slots` без `on_time_upstream_pending`; знаменатель условной надёжности парсера. |
| `parser_on_time_fresh_rate_pct` | Доля своевременных fresh-публикаций после исключения только независимо доказанного отсутствия upstream. |

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
      "dataset_fetched_at": "2026-06-07T21:31:29.446191+00:00",
      "upstream_freshness": {
        "status": "fresh",
        "age_seconds": 3600,
        "body_as_of": "2026-06-07T20:31:29.446191+00:00"
      },
      "fresh_only_eligible": true
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

Если live refresh упал, но старый cache рабочий, endpoint обычно может продолжать
отдавать старый dataset. В status тогда появляются `serving_cached_dataset`,
`effective_state=ok_cached`, `last_refresh_state`, `last_refresh_error`,
`cached_dataset_age_hours`. Это не ломает публичный `/health`, но видно в
`/ops/health`, `/ops/summary` и `python -m app.cli freshness-check`. Исключение —
четыре HSReplay daily meta-среза: `hsreplay_meta_archetypes_legend_eu_1d`,
`hsreplay_meta_top_1000_legend_1d_firecrawl`, `hsreplay_meta_legend_1d_firecrawl`
и `hsreplay_meta_diamond_4to1_1d_firecrawl`. Для них старый или непроверенный
LKG не считается fresh-only: при отсутствии
`data.structured.upstream_freshness.status="fresh"` dataset endpoint возвращает
`503` с причиной, а не маскирует проблему успешным HTTP 200.

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

## HSGuru Archetype Catalogs

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsguru/archetypes` | Каталог архетипов текущего окна; с `period=black_empire_ban` — окна Bans (`meta.available_periods`). Фильтры: `format`, `q`, `min_games` (по умолчанию 10), `has_decks`, `sort`, `order`, `limit`, `offset` |

Строка архетипа: `format`, `archetype`, `games`, `winrate`, `popularity_pct`,
`avg_turns`, `avg_duration_minutes`, `climbing_speed_stars_per_hour`,
`archetype_url`, `decks_url`, `decks[]`, `deck_count`, `has_decks` и поля
происхождения сборок:

- `decks_period` — окно, из которого взяты сборки (`null` без сборок);
- `decks_source` — `stats` (страница `/decks`, сборки от 50 игр), `replays`
  (реальные колоды из публичных реплеев, без `games`/`win_rate`) или
  `mixed`;
- `decks_fallback` — `true`, если сборки не из окна каталога или из реплеев.

Подробнее: [DATA_CATALOG.md](DATA_CATALOG.md).

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsguru/archetypes?period=black_empire_ban&format=standard&limit=5" \
  | jq '.data[] | {archetype, games, deck_count, decks_period, decks_source, decks_fallback}'
```

## HSGuru Matchup Matrices

Подробно (что отдаёт HSGuru, срезы, TTL, расход Scrape.do): [HSGURU_MATCHUPS.md](HSGURU_MATCHUPS.md).

Источник `hsguru_matchups` (pipeline). Таймер
`hs-data-api-docker-refresh-hsguru-matchups.timer` каждый час в :10 читает
просроченные срезы `/matchups` через Scrape.do без браузера (1 кредит,
5 параллельно), FlareSolverr — запасной маршрут.

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsguru/matchups` | Матрица одного среза: `archetypes[]` (`archetype`, `class`, `games`, `winrate`, `popularity_pct`) по убыванию игр и `matchups[]` (`archetype`, `opponent`, `winrate`, `games`); `total_games`, `checked_at`, `changed_at`, `stale`, `source_url`. `meta.available` — форматы, ранги, периоды, монетка, пресеты порогов, `default_period`, `current_period` |
| `GET /v1/hsguru/matchups/slices` | Все срезы с состоянием, временем чтения, числом архетипов и ячеек; `meta.last_run` и `meta.estimate` (кредиты в сутки) |

Параметры: `format` (`standard`, `wild`), `rank` (`all`, `top_legend`,
`top_5k`, `legend`, `diamond_to_legend`, `diamond_4to1`, `bronze_plat`),
`period` (из `meta.available.periods`; по умолчанию окно банов), `coin`
(только `any_player`: HSGuru не публикует матчапы по монетке, другие значения
— `422`), `min_games` (минимум игр ячейки, от 1), `min_archetype_games`
(минимум игр архетипа, от 1), `limit` (N самых популярных архетипов, до 200).
Несобранный срез — `404`.

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsguru/matchups?format=standard&rank=legend&period=black_empire_ban&min_games=100&limit=2"
```

```json
{
  "data": {
    "format": "standard",
    "rank": "legend",
    "period": "black_empire_ban",
    "coin": "any_player",
    "min_games": 100,
    "min_archetype_games": 1,
    "total_games": 936588,
    "checked_at": "2026-10-25T16:31:28.884000+00:00",
    "changed_at": "2026-10-25T16:31:28.884000+00:00",
    "stale": false,
    "source_url": "https://www.hsguru.com/matchups?format=2&rank=legend&period=black_empire_ban&min_archetype_sample=1&min_matchup_sample=1",
    "archetypes": [
      {"archetype": "Aggro Warlock", "class": "WARLOCK", "games": 182401, "winrate": 56.5, "popularity_pct": 19.48},
      {"archetype": "C'Thun DH", "class": "DEMONHUNTER", "games": 121862, "winrate": 51.0, "popularity_pct": 13.01}
    ],
    "matchups": [
      {"archetype": "Aggro Warlock", "opponent": "Aggro Warlock", "winrate": 50.0, "games": 41382},
      {"archetype": "Aggro Warlock", "opponent": "C'Thun DH", "winrate": 54.3, "games": 26669},
      {"archetype": "C'Thun DH", "opponent": "Aggro Warlock", "winrate": 45.7, "games": 26669},
      {"archetype": "C'Thun DH", "opponent": "C'Thun DH", "winrate": 50.0, "games": 18124}
    ]
  },
  "meta": {
    "source_id": "hsguru_matchups",
    "fetched_at": "2026-10-25T16:31:28.884000+00:00",
    "stale": false,
    "count": 4,
    "available": {
      "formats": ["standard", "wild"],
      "ranks": ["all", "top_legend", "top_5k", "legend", "diamond_to_legend", "diamond_4to1", "bronze_plat"],
      "periods": ["past_3_hours", "past_6_hours", "past_day", "past_3_days", "past_week", "past_2_weeks", "past_30_days", "patch_36.6.3", "black_empire", "black_empire_ban"],
      "coins": ["any_player"],
      "min_games": [1, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
      "min_archetype_games": [1, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000],
      "current_period": "black_empire",
      "default_period": "black_empire_ban"
    }
  }
}
```

`/v1/hsguru/matchups/slices` отдаёт в `data[]` по строке на срез: `key`,
`format`, `rank`, `period`, `coin`, `active`, `state` (`ok`, `no_data`,
`failed`, `unavailable`, `pending`), `checked_at`, `changed_at`,
`total_games`, `archetypes`, `cells`, `stale`, `last_error`; в `meta` —
`available`, `last_run` (страницы, кредиты, `stop_reason`) и `estimate`
(кредиты в сутки по TTL).

## HSGuru Mulligan And Class Matchups

Подробное описание (что отдаёт HSGuru, срезы, очередь обновления, бюджет
Scrape.do, все параметры и поля): [HSGURU_MULLIGAN.md](HSGURU_MULLIGAN.md).

Источник `hsguru_mulligan` (pipeline). Таймер
`hs-data-api-docker-refresh-hsguru-mulligan.timer` каждый час читает самые
просроченные страницы `/card-stats`, `/archetype/<name>` и `/deck/<id>` для
архетипов `hsguru_meta_matrix` и колод каталогов HSGuru. Страницы идут
через Scrape.do по 40 параллельно, сначала без браузера (1 кредит);
FlareSolverr — запасной маршрут.

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsguru/mulligan` | Муллиган одного среза: `rows[]` с `card_id`, `dbf_id`, `card_name`, `mulligan_impact/count`, `drawn_impact/count`, `not_drawn_impact/count`, `kept_impact/count`; `games`, `changed_at`, `checked_at`, `stale` |
| `GET /v1/hsguru/class-matchups` | `rows[]` с `opponent_class`, `winrate`, `games`, `share_pct`; итог в `games`/`winrate` |
| `GET /v1/hsguru/mulligan/slices` | Какие срезы сохранены у архетипа или колоды |
| `GET /v1/hsguru/mulligan/history` | Снимки среза; с `card` — строка карты в каждом снимке |
| `GET /v1/hsguru/mulligan/coverage` | Покрытие и последние запуски с расходом Scrape.do (без кэша) |

Срез: ровно один из `archetype` (с `format`) или `deck_id`; `rank` (`all`,
`diamond_4to1`, `diamond_to_legend`, `legend`, `top_5k`, `top_legend`),
`period` (период HSGuru, включая `*_ban`, или `current`), `coin`
(`any_player`, `on_coin`, `going_first`), `opponent_class` (`any` или класс
заглавными буквами). Несобранный срез — `404`.

## HSGuru Brawl And Esports

Описание наборов, полей и аудит покрытия HSGuru:
[DATA_CATALOG.md](DATA_CATALOG.md#hsguru-потасовка-и-киберспорт).

Источники `hsguru_brawl` (таймер `hs-data-api-docker-refresh-hsguru-brawl.timer`,
каждые 3 часа), `hsguru_esports_tournaments`, `hsguru_esports_points` и
`hsguru_esports_legacy` (таймер `hs-data-api-docker-refresh-hsguru-esports.timer`,
ежедневно; архив — не чаще раза в неделю). Вручную:
`python -m app.cli refresh-hsguru-brawl`,
`python -m app.cli refresh-hsguru-esports [--part tournaments|points|legacy] [--force-legacy]`.

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsguru/brawl/meta` | `items[]` архетипов Потасовки: `archetype`, `winrate`, `popularity`, `games`, `turns`, `duration_minutes`, `climbing_speed`; `rank`, `coin`, `opponent_class`, `total_games` |
| `GET /v1/hsguru/brawl/decks` | `items[]` колод: `archetype`, `class`, `deck_code`, `win_rate`, `games`, `url` |
| `GET /v1/hsguru/esports/upcoming` | `items[]`: `tournament_id`, `name`, `start_time`, `tags`, `url` |
| `GET /v1/hsguru/esports/tournaments` | Сводки турниров (пагинация): `winner`, `stages`, `players`, `top_archetypes`, `complete` |
| `GET /v1/hsguru/esports/tournaments/{tournament_id}` | Полный турнир: `stages[].standings[]`, `lineups[].decks[]`, `lineup_popularity`; неизвестный id — `404` |
| `GET /v1/hsguru/esports/points` | Сезон очков: `headers`, `items[]` (`rank`, `player`, колонки месяцев/турниров, `total`); без `season` — текущий год |
| `GET /v1/hsguru/esports/points/seasons` | Пары сезон × лидерборд и число игроков |
| `GET /v1/hsguru/esports/legacy/{table}` | `mt-stats`, `qualifier-stats` (`selector` — период), `mt-points` (`selector` — сезон); `options` — доступные значения |

Невалидный параметр — `422`, ещё не опубликованный набор — `503`. Все ответы —
конверт `/v1`: `data` и `meta` (`source_id`, `fetched_at`, `stale`, `count`,
для списков с пагинацией — `limit`, `offset`). Эксплуатация, расход и
ограничения: [HSGURU_BRAWL_ESPORTS.md](HSGURU_BRAWL_ESPORTS.md).

### `GET /v1/hsguru/brawl/meta`

| Parameter | Default | Описание |
| --- | --- | --- |
| `rank` | `all` | `all`, `top_legend` (Top 1k), `top_5k`, `legend`, `diamond_4to1`, `diamond_to_legend`, `bronze_plat`. В Потасовке почти все игры попадают в `all`. |
| `coin` | `any_player` | `any_player`, `on_coin`, `going_first`. |
| `opponent_class` | `any` | `any` или класс заглавными буквами (`MAGE`); собирается для `rank=all`. |
| `min_games` | `10` | Отбросить архетипы с меньшим числом игр. |

```json
{
  "data": {
    "rank": "all", "coin": "any_player", "opponent_class": "any",
    "min_games": 10, "total_games": 11740,
    "source_url": "https://www.hsguru.com/meta?format=-1&period=brawl&rank=all&min_games=10",
    "items": [
      {"archetype": "C'Thun Priest", "winrate": 72.6, "popularity": 21.0, "games": 2525,
       "turns": 9.2, "duration_minutes": 8.5, "climbing_speed": 3.21}
    ]
  },
  "meta": {"source_id": "hsguru_brawl", "fetched_at": "2026-10-25T16:28:34+00:00", "stale": false, "count": 72}
}
```

### `GET /v1/hsguru/brawl/decks`

| Parameter | Default | Описание |
| --- | --- | --- |
| `rank` | `all` | `all`, `legend`, `diamond_to_legend`. |
| `class` | — | Класс (`Priest`), без учёта регистра. |
| `archetype` | — | Точное название архетипа, без учёта регистра. |

```json
{
  "data": {
    "rank": "all", "min_games": 50,
    "items": [
      {"archetype": "Shaman", "class": "Shaman", "format": "Brawl",
       "deck_code": "AAEBAaoIAA/X6Qf87gfF8Afr8Af79QeT9geW+geJ+we3/ge//wfA/wf1/weAgQjegQidgggAAA==",
       "win_rate": 60.5, "games": 816, "url": "https://www.hsguru.com/deck/42479368"}
    ]
  },
  "meta": {"source_id": "hsguru_brawl", "stale": false, "count": 13}
}
```

### `GET /v1/hsguru/esports/upcoming`

Без параметров.

```json
{
  "data": {"items": [
    {"tournament_id": "6acf43976090a5001347461c", "name": "Aventa Cup #3 - 50€ Cashprize",
     "start_time": "2026-10-23T17:00:17+00:00", "tags": ["Open", "Bo3", "Europe"],
     "url": "https://www.hsguru.com/battlefy/tournament/6acf43976090a5001347461c"}
  ]},
  "meta": {"source_id": "hsguru_esports_tournaments", "stale": false, "count": 15}
}
```

### `GET /v1/hsguru/esports/tournaments`

| Parameter | Default | Описание |
| --- | --- | --- |
| `kind` | — | `tour_stop` (официальные MT) или `community` (стартовавшие за 14 дней из `/esports`). |
| `limit` | `50` | 1–500. |
| `offset` | `0` | Смещение. |
## HSGuru Streamers

Подробно: [HSGURU_STREAMERS.md](HSGURU_STREAMERS.md). Источник
`hsguru_streamers` (pipeline). Таймер
`hs-data-api-docker-refresh-hsguru-streamers.timer` раз в 5 минут читает:

* `/streaming-now`;
* новые строки `/streamer-decks` — все форматы, ранги и окна;
* профили каналов Twitch — аватар, YouTube, X, Telegram и записи
  трансляций для VOD-ссылок.

Ответ — конверт `{ data, meta }`. `meta.count` — всего строк, которые
подходят под фильтры. `meta.fetched_at` — окончание последнего запуска.
`meta.stale` выставляется, если данные старше `stale_hours` источника (2 ч).

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsguru/streamers` | Стримеры с ссылками, эфиром и последними колодами |
| `GET /v1/hsguru/streamers/{streamer}` | Профиль, эфир, колоды по формату и классу (`facets`), записи (`vods`), последняя колода (`last_deck`) |
| `GET /v1/hsguru/streamers/{streamer}/decks` | Колоды стримера, новые сверху; в ответе ещё `streamer` |
| `GET /v1/hsguru/streamer-decks` | Колоды всех стримеров, новые сверху; у каждой `streamer` (`id`, `slug`, `display_name`, `avatar_url`) |
| `GET /v1/hsguru/streaming-now` | Кто в эфире, с какой колодой и в каком режиме; `meta.observed_at`, `meta.stale` (старше 20 минут); без кэша |
| `GET /v1/hsguru/streamers/coverage` | Число стримеров, профилей, колод, VOD, эфиров и последние запуски с расходом Scrape.do; без кэша |

`{streamer}` — Twitch login, ник HSGuru (без учёта регистра) или Twitch user
id. Неизвестный стример — `404`.

Параметры:

| Параметр | Где | Значения |
| --- | --- | --- |
| `format` | все списки | `standard`, `wild`, `classic`, `twist` |
| `player_class` | все списки | класс заглавными: `PRIEST`, `DEATHKNIGHT`, … |
| `days` | `/streamers` (30), `/streamers/{s}/decks` (0), `/streamer-decks` (7) | последняя игра за N дней; `0` — за всё время |
| `q` | `/streamers` | поиск по нику или логину |
| `language` | `/streamers`, `/streaming-now` | двухбуквенный язык эфира; HSGuru даёт только языки из `HS_HSGURU_STREAMERS_LANGUAGES` (по умолчанию `ru`) |
| `live` | `/streamers` | `true` — только те, кто сейчас в эфире |
| `sort` | `/streamers` | `active` (по умолчанию: в эфире, потом недавние), `followers`, `decks`, `rank` |
| `decks` | `/streamers` | сколько последних колод у каждого (0–10, по умолчанию 3) |
| `archetype`, `max_rank` | `/streamer-decks` | точное имя архетипа HSGuru; пик Легенды не хуже ранга |
| `mode`, `known` | `/streaming-now` | режим (`standard`, `battlegrounds`, …); `true` — только стримеры с колодами на HSGuru |
| `limit`, `offset` | все списки | страницы; `limit` до 100 (`/streamers`), 200 (колоды), 300 (эфиры) |

Колода: `deck_id`, `deck_code`, `archetype` (имя HSGuru), `player_class`,
`format`, `peak_rank`, `latest_rank`, `worst_rank` (`null` вне Легенды),
`wins`, `losses`, `games`, `winrate`, `last_played_at`, `first_seen_at`,
`hsguru_url`, `on_current_stream`, `stream_url`, `vod` (`url` с таймкодом,
`title`, `started_at`, `offset_seconds` — или `null`).

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsguru/streamers?days=0&limit=1&decks=1" | jq .
```

Ответ живого прогона 25.10.2026; название эфира сокращено:

```json
{
  "data": [
    {"tournament_id": "6a6c88f821e3c30013db3d46",
     "name": "2026 Hearthstone Masters Tour Summer Championship", "kind": "tour_stop",
     "start_time": "2026-07-31T14:00:00+00:00", "complete": true,
     "players": 16, "lineups_complete": true,
     "stages": ["Group A", "Group B", "Group C", "Group D", "Top 8"],
     "winner": {"name": "demoshadow", "country": "UA"},
     "top_archetypes": [{"archetype": "Void Soul DH", "lineups": 16, "share": 1.0}]}
  ],
  "meta": {"source_id": "hsguru_esports_tournaments", "stale": false, "count": 32, "limit": 1, "offset": 0}
}
```

### `GET /v1/hsguru/esports/tournaments/{tournament_id}`

`tournament_id` — id Battlefy из списка. Неизвестный id — `404`.

```json
{
  "data": {
    "tournament_id": "6a6c88f821e3c30013db3d46", "kind": "tour_stop",
    "stages": [{"stage_id": "6a6f3a399e7a410019bde4e0", "name": "Top 8",
                "standings": [{"place": 1, "player": {"name": "demoshadow", "country": "UA"},
                               "score": "3 - 0", "wins": 3, "losses": 0}]}],
    "lineups": [{"player": {"name": "Che0nsu", "country": "KR"},
                 "decks": [{"archetype": "Void Soul DH", "class": "DEMONHUNTER",
                            "deck_code": "AAECAea5AwaongbNngaKqgeSqgeTqgfosQcM4fgFzp4G0J4G054G3v8GtpcHkr8Hlb8HxN0Hxt0Hx90HteEHAAA=",
                            "url": "https://www.hsguru.com/deck/41269302"}]}],
    "lineups_total": 16, "lineups_complete": true,
    "lineup_popularity": {"lineups": 16,
      "archetypes": [{"archetype": "Void Soul DH", "lineups": 16, "share": 1.0}],
      "classes": [{"class": "DEMONHUNTER", "lineups": 16, "share": 1.0}]},
    "complete": true, "reused": true
  },
  "meta": {"source_id": "hsguru_esports_tournaments", "stale": false, "count": 1}
}
```

### `GET /v1/hsguru/esports/points`

| Parameter | Default | Описание |
| --- | --- | --- |
| `season` | последний годовой | `2026`, `2026_summer`, `china_2026_spring` …; список — `/points/seasons`. |
| `leaderboard` | `STD` | `STD` или `BG_LL` (Battlegrounds). |
| `region` | — | `EU`, `AM`, `AP`, `CN`. |
| `limit` / `offset` | `500` / `0` | `limit` 1–3000. |

```json
{
  "data": {
    "season": "2026", "leaderboard": "STD", "leaderboard_label": "Standard",
    "headers": ["#", "Player", "January", "February", "March", "April", "May", "June", "Total"],
    "items": [{"rank": 1,
               "player": {"name": "Gaby59", "battletag": "Gaby59", "region": "EU", "country": "FR",
                          "profile_url": "https://www.hsguru.com/player-profile/Gaby59"},
               "january": 6, "february": 7, "march": 7, "april": 7, "may": 7, "june": 6, "total": 40}]
  },
  "meta": {"source_id": "hsguru_esports_points", "stale": false, "count": 227, "limit": 1, "offset": 0}
}
```

### `GET /v1/hsguru/esports/points/seasons`

```json
{
  "data": [{"season": "2026", "season_label": "2026", "leaderboard": "STD",
            "leaderboard_label": "Standard", "players": 1182}],
  "meta": {"source_id": "hsguru_esports_points", "stale": false, "count": 36}
}
```

### `GET /v1/hsguru/esports/legacy/{table}`

| Parameter | Default | Описание |
| --- | --- | --- |
| `table` | — | `mt-stats`, `qualifier-stats`, `mt-points`. |
| `selector` | `all` / новейший сезон | `qualifier-stats`: `current`, `all`, год или тур-стоп; `mt-points`: сезон (`2022_fall`). Доступные значения — `data.options`. |
| `limit` / `offset` | `500` / `0` | `limit` 1–5000. |

```json
{
  "data": {
    "table": "qualifier-stats", "options": ["current", "all", "2020", "2021", "2022"],
    "period": "2021", "headers": ["#", "Player", "Cups", "Top 8", "Top 16", "Winrate %"],
    "items": [{"rank": 1, "player": {"name": "Rine", "battletag": "Rine#21929"},
               "cups": 108, "top_8": 15, "top_16": 18, "winrate_pct": 70.57}]
  },
  "meta": {"source_id": "hsguru_esports_legacy", "stale": false, "count": 753, "limit": 1, "offset": 0}
}
## HSReplay Mulligan v2 (deck mulligan guide)

Подробно (эндпоинт HSReplay, набор колод, очередь, бюджет Scrape.do,
премиум-сессия, все поля, сравнение с HSGuru):
[HSREPLAY_MULLIGAN.md](HSREPLAY_MULLIGAN.md).

Источник `hsreplay_mulligan` (pipeline). Таймер
`hs-data-api-docker-refresh-hsreplay-mulligan.timer` каждые 3 часа (в :50 с
01:50) читает просроченные срезы `https://hsreplay.net/api/v1/mulligan/` для
топ-колод HSReplay Стандарта и Вольного на Легенде, Алмазе 4–1, Алмазе и
Бронзе–Золоте: `ALL`/`FIRST`/`COIN` × `last_30_days`/`current_patch`/
`current_expansion`. Запросы идут через Scrape.do без рендера (1 кредит),
не больше 5 параллельно, около 150 кредитов в сутки (UTC).

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsreplay/mulligan` | Муллиган одного среза колоды: `source: "hsreplay"`, `games`, `base_winrate`, `scope` (`deck`/`archetype`/`class`), `deck` (архетип, класс, список), `opponent_classes`, `premium_session`, `rows[]` с `dbf_id`, `card_id`, `card_name`, `keep_percentage`, `opening_hand_winrate`, `winrate_when_drawn`, `winrate_when_played`, `avg_turns_in_hand`, `avg_turn_played_on` и `opening_hand_impact`/`drawn_impact`/`played_impact` |
| `GET /v1/hsreplay/mulligan/decks` | Набор колод по формату и рангу: архетип, класс, игры, винрейт, причина выбора (`popular`/`winrate`), сколько срезов собрано |
| `GET /v1/hsreplay/mulligan/slices` | Все срезы одной колоды: состояние, последняя проверка, премиум или нет |
| `GET /v1/hsreplay/mulligan/history` | Снимки среза; с `card` — строка карты в каждом снимке |
| `GET /v1/hsreplay/mulligan/coverage` | Покрытие, статус премиум-сессии, последние запуски с расходом Scrape.do (без кэша) |

Срез: `deck` (shortid HSReplay, обязательно), `format` (`standard`/`wild`,
необязательно), `rank` (`legend`, `diamond_4to1`, `diamond`, `bronze_gold`),
`initiative` (`all`, `first`, `coin`), `period` (`last_30_days`,
`current_patch`, `current_expansion`), `opponent_class` (`any` или класс
заглавными буквами). Несобранный срез — `404`. Премиум-срез, который нельзя
собрать или отдать из-за отказа HSReplay в сессии (401/403, аккаунт без
премиума, нет cookie), — `503` с `detail.premium_session`; пустой ответ
молча не отдаётся. Если у колоды в срезе нет игр (HSReplay 204), ответ `200`
с `no_data: true` и пустым `rows`.

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsreplay/mulligan?deck=5Tf8XCaMoKKU7Fk4yaqa8c&rank=legend&initiative=coin" \
  | jq '.data | {source, games, base_winrate, scope, top: .rows[:3]}'
```
    {
      "id": 86728139,
      "slug": "moquaitv",
      "display_name": "MoquaiTV",
      "login": "moquaitv",
      "twitch_url": "https://www.twitch.tv/moquaitv",
      "youtube_url": null,
      "x_url": null,
      "links": [],
      "avatar_url": null,
      "followers": null,
      "language": null,
      "decks_total": 102,
      "last_played_at": "2026-10-25T16:24:05+00:00",
      "hsguru_url": "https://www.hsguru.com/streamer-decks?twitch_id=86728139",
      "live": {
        "platform": "twitch",
        "title": "[EU] Legend | WarlockStone",
        "viewers": 14,
        "mode": null,
        "language": null,
        "started_at": "2026-10-25T15:54:23.312736+00:00",
        "thumbnail_url": "https://static-cdn.jtvnw.net/previews-ttv/live_user_moquaitv-384x216.jpg",
        "stream_url": "https://www.twitch.tv/moquaitv",
        "observed_at": "2026-10-25T16:24:11.312736+00:00"
      },
      "window": { "decks": 33, "best_rank": 609, "last_played_at": "2026-10-25T16:24:05+00:00" },
      "recent_decks": [
        {
          "deck_id": 42692433,
          "deck_code": "AAECAa0GAufYB/OJCAnwnwSFhgevyQes2Qf04Afo8Qfh+gf7iQj8iQgAAA==",
          "archetype": "Thief Priest",
          "player_class": "PRIEST",
          "format": "standard",
          "peak_rank": 6623,
          "latest_rank": null,
          "worst_rank": 8560,
          "wins": 11,
          "losses": 8,
          "games": 19,
          "winrate": 57.9,
          "last_played_at": "2026-10-25T16:24:05+00:00",
          "hsguru_url": "https://www.hsguru.com/deck/42692433",
          "on_current_stream": true,
          "stream_url": "https://www.twitch.tv/moquaitv",
          "vod": null
        }
      ]
    }
  ],
  "meta": { "source_id": "hsguru_streamers", "fetched_at": "2026-10-25T16:26:22.512647+00:00", "stale": false, "count": 226, "limit": 1, "offset": 0 }
}
```

Колода с записью трансляции (`/v1/hsguru/streamers/nohandsgamer/decks`):

```json
{
  "deck_id": 42709303,
  "archetype": "Egg Priest",
  "player_class": "PRIEST",
  "format": "standard",
  "peak_rank": 20,
  "wins": 33,
  "losses": 17,
  "winrate": 66.0,
  "last_played_at": "2026-10-25T00:42:46+00:00",
  "on_current_stream": false,
  "vod": {
    "url": "https://www.twitch.tv/videos/2909721478?t=5h41m20s",
    "title": "Egg Priest going for #1 Legend !hsreplay",
    "started_at": "2026-10-24T18:56:26+00:00",
    "offset_seconds": 20480
  }
}
```

Эфир (`/v1/hsguru/streaming-now`): поля `live`, а ещё `login`,
`display_name`, `mode`, `mode_source` (`hsguru` или `deck`, если режим взят из
формата колоды эфира), `streamer` (профиль или `null`) и `current_deck`
(колода, сыгранная после начала эфира, или `null`).

## Official Blizzard Leaderboards

Подробно (охват, частота, вежливость к API, хранение, поля):
[BLIZZARD_LEADERBOARDS.md](BLIZZARD_LEADERBOARDS.md).

Источник `blizzard_leaderboards` (pipeline). Таймер
`hs-data-api-docker-refresh-blizzard-leaderboards.timer` каждые 6 часов
полностью перечитывает текущие сезоны всех режимов и регионов;
`hs-data-api-docker-refresh-blizzard-leaderboards-history.timer` каждый час,
кроме часов живого обхода, дочитывает прошлые сезоны (каждый — один раз).

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/leaderboards` | `board`, `snapshot` (`day`, `captured_at`, `total_size`, `stored_rows`, `truncated`, `stale`), `days[]`, `total`, `rows[]` с `rank`, `name`, `rating`, `previous_rank` |
| `GET /v1/leaderboards/stats` | `top_rating`, `thresholds[]` (`rank` 100/1000/5000, `reached`, `rating`), `rating` (min/max/mean/median), `histogram[]` (`from`, `to`, `players`), `series[]` по дням |
| `GET /v1/leaderboards/players` | `[{region, name, current: [{mode, season_id, rank, rating}]}]` |
| `GET /v1/leaderboards/player` | `player`, `shared_name`, `seasons[]` с `best_rank`, `last_rank`, `last_rating`, `days`, `series[]` (`day`, `rank`, `rating`, `total_size`, `final`) |
| `GET /v1/leaderboards/seasons` | `modes[].regions.{EU,US,AP}.seasons[]` с `has_data`, `final`, `is_latest`, `total_size` |
| `GET /v1/leaderboards/coverage` | Прогресс по режимам и регионам и последние запуски (без кэша) |

`mode`: `standard`, `wild`, `battlegrounds`, `battlegroundsduo`, `arena`,
`undergroundarena`, `twist`; `region`: `EU`, `US`, `AP`. Несобранная таблица —
`404`.

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

## HSReplay Battlegrounds Minion Statistics

Подробное описание с параметрами, всеми полями, формулами, ошибками и примерами:
[BG_MINION_STATS.md](BG_MINION_STATS.md).

Источник `hsreplay_battlegrounds_minion_stats` (pipeline). Раз в сутки
(`hs-data-api-docker-refresh-bg-minion-stats.timer`, 16:20 Europe/Warsaw)
CLI `python -m app.cli refresh-bg-minion-stats --scheduled` забирает четыре
analytics-запроса HSReplay. Каждый отдаёт сразу все существа среза:

| Запрос | Что хранится |
| --- | --- |
| `battlegrounds_minion_list` | Итоги и ходы 1–16 обычной и золотой версии: игры с существом и без, суммы мест, победы/поражения боёв, `at_least_one`. |
| `battlegrounds_minion_position` | Позиции 1–7 на доске (обычная и с провокацией), суммированные по составу. |
| `battlegrounds_purchase_rates_by_turn` | Сколько раз существо предлагали и брали при 0, 1, 2 копиях, включая «Раскопку». |
| `battlegrounds_minion_stats` | Медианные атака и здоровье по ходам. |

Запросы идут прямо в JSON HSReplay через `app.hsreplay_client` с Premium-сессией
(FlareSolverr, затем Scrape.do без рендеринга как запасной канал). Платные вызовы
пишутся в provider-usage.

**Срезы.** Позиции весят около 24 МБ на срез, поэтому их собирает только
основной MMR:

| `mmr` | `period` | Позиции |
| --- | --- | --- |
| `TOP_50_PERCENT` | `LAST_7_DAYS` (по умолчанию) | да |
| `TOP_50_PERCENT` | `CURRENT_BATTLEGROUNDS_PATCH` | да |
| `TOP_20_PERCENT`, `ALL` | `LAST_7_DAYS`, `CURRENT_BATTLEGROUNDS_PATCH` | нет (`positions: null`) |

`TOP_5_PERCENT` и `TOP_1_PERCENT` HSReplay для этих запросов не отдаёт
(`not supported or enabled for query`), поэтому API их не принимает (`422`).

**Проверки перед публикацией.** Каждый срез проверяется отдельно:

- схема каждого ответа (типы, раунды 1–16, суммы мест 1–8 на игру, пары победа/поражение, дубликаты);
- не меньше 150 существ и падение не больше 35 % к прошлому снимку;
- популярность заполнена у 80 % существ;
- покрытие: покупки у 75 % существ, медианы у 80 %, позиции у 80 %.

Срез с ошибкой не пишется: API продолжает отдавать его прошлый снимок
(last-known-good, `serving_cached_dataset` в статусе источника). Если
`as_of` всех запросов не изменился, новый снимок не создаётся.

**Хранение и история.** SQLite `bg_minion_stat_snapshots`, `_summaries`,
`_rounds`, `_positions`, `_purchases`, `_medians` (одна транзакция на
снимок). Инкрементальная теневая синхронизация зеркалит их в PostgreSQL
`analytics.bg_minion_stat_*`; там же представления `hub.bg_minion_stat_latest` и
`hub.bg_minion_stat_history`. Снимки и итоги хранятся бессрочно. Ходы хранятся
60 дней (`HS_BG_MINION_STATS_ROUND_RETENTION_DAYS`). Позиции, покупки и медианы
хранятся 30 дней (`HS_BG_MINION_STATS_DETAIL_RETENTION_DAYS`, минимум 7).
Последний снимок каждого среза ретенция не трогает.

**Формулы** повторяют страницу HSReplay (проверено на dbf 104551, поле
`methodology` в ответе). Вес хода r: `distinct_games / pct_players_remaining[r]`.

- `popularity` = Σ `at_least_one`·вес / Σ вес.
- `impact` = Σ (среднее место без − с) · `games_with`/`pct_remaining` / Σ `games_with`/`pct_remaining`.
- `combat_winrate` = 100·победы/(победы+поражения).

Итог равен `null`, если выборка меньше 30. По ходам: влияние и популярность
скрыты, когда игр с существом или без него меньше 30; доля побед скрыта при
боях < 30. Частота покупки k‑й копии: `picked_{k-1}/offered_{k-1}`, если
предложений > 30. Позиции: `composition=0` суммирует все строки существа, как
«Все составы» на сайте; иначе суммируются строки этого состава. Влияние
позиции скрыто при < 200 появлений. HSReplay отдаёт и строки‑итоги
(состав или соперник `-1`), а сайт суммирует их вместе с остальными. Поэтому
`occurrences` при `composition=0` больше реального числа досок. Доли и влияние
от этого не меняются. Рядом с каждым производным значением отдаются исходные
счётчики. PostgreSQL хранит значения как `real`, поэтому GraphQL может
отличаться от REST в последнем знаке.

Если пул существ легитимно сократился больше чем на 35 % (ротация), разовый
прогон с `HS_BG_MINION_STATS_ALLOW_POOL_DROP=1` снимает защиту от регрессии.
Деградированный ответ без каталога (`images: null`) приходит с
`Cache-Control: no-store`. Старый `snapshot_id`, у которого детали уже удалены
ретенцией, отдаёт `404`; итоги таких снимков есть в `/stats/history`.

### `GET /v1/battlegrounds/minions/{minion}/stats`

`minion` — dbf (`104551`) или card id (`BG28_300`; золотой `BG28_300_G`
приводится к обычному). Параметры: `mmr`, `period`, `composition` (id состава
для позиций, `0` — все), `snapshot_id` (старый снимок того же среза). Имена и
картинки берутся из нашего каталога `catalog.battlegrounds_cards` по dbf.
Если PostgreSQL недоступен, имена берутся из HearthstoneJSON, а `images` будет `null`.

```json
{
  "data": {
    "minion": {
      "dbf_id": 104551, "card_id": "BG28_300", "name": "Harmless Bonehead",
      "name_ru": "Безобидная черепушка", "tavern_tier": 1, "minion_types": ["UNDEAD"],
      "golden_dbf_id": 104554, "catalog": true,
      "images": {
        "card": "https://api.kolodahearthstone.com/uploads/framed/BG28_300.png",
        "card_golden": "https://api.kolodahearthstone.com/uploads/golden/BG28_300-….png",
        "art": "https://api.kolodahearthstone.com/uploads/art/BG28_300.jpg",
        "horizontal": null
      }
    },
    "slice": {
      "snapshot_id": 1, "mmr_percentile": "TOP_50_PERCENT", "time_range": "LAST_7_DAYS",
      "fetched_at": "2026-10-21T16:30:00+00:00",
      "as_of": {"minion_list": "2026-10-20T21:02:05Z", "positions": "2026-10-21T13:41:22Z",
                "purchases": "2026-10-21T13:41:24Z", "medians": "2026-10-21T07:17:54Z"}
    },
    "normal": {"impact": -0.0144, "combat_winrate": 56.83, "popularity": 1.885,
               "games_with_minion": 18161, "games_without_minion": 974046, "wins": 16625, "losses": 12629},
    "golden": {"impact": -0.0874, "combat_winrate": 51.724, "popularity": 0.217, "...": "..."},
    "rounds": [
      {"combat_round": 1,
       "normal": {"impact": 0.021, "combat_winrate": 61.9, "popularity": 6.01,
                  "games_with_minion": 7905, "games_without_minion": 191262, "distinct_games": 108824,
                  "wins": 2409, "losses": 1484, "at_least_one": 6.01},
       "golden": null}
    ],
    "positions": {
      "composition_id": 0, "default_board": "normal",
      "normal": {"occurrences": 50688, "positions": [
        {"position": 1, "popularity": 6.739, "impact": -0.2602, "occurrences": 3416, "sum_placement": 21132}
      ]},
      "taunt": {"occurrences": 1687, "positions": ["..."]},
      "compositions": [{"composition_id": 2, "name": "…"}, {"composition_id": 47, "name": "…"}]
    },
    "purchases": [
      {"recruitment_round": 1, "first_copy": 20.874, "second_copy": null, "third_copy": null,
       "discover": {"first_copy": 27.651, "second_copy": null, "third_copy": null},
       "counts": {"times_offered_0_owned": 124122, "times_picked_0_owned": 25909, "...": 0}}
    ],
    "medians": [{"combat_round": 16, "normal": {"attack": 8.0, "health": 1.0},
                 "golden": {"attack": 67.0, "health": 21.0}}],
    "methodology": {"formula_source": "hsreplay.net minion page bundle, verified 2026-10-21 on dbf 104551"}
  },
  "meta": {"source_id": "hsreplay_battlegrounds_minion_stats", "fetched_at": "…", "stale": false, "count": 1}
}
```

`404`: неизвестное существо или нет снимка для среза. `422`: неверный `mmr` или `period`.

### `GET /v1/battlegrounds/minions/stats`

Сводка по всем существам последнего снимка среза: `dbf_id`, `card_id`,
`name`, `name_ru`, `tavern_tier`, `images`, `normal`, `golden`. Параметры:
`mmr`, `period`, `tavern_tier`, `sort` (`popularity`, `impact`,
`combat_winrate`, `games_with_minion`, `tavern_tier`; по обычной версии),
`limit` ≤ 500, `offset`. `meta.count` — число существ в срезе.

### `GET /v1/battlegrounds/minions/{minion}/stats/history`

Точки по снимкам (`snapshot_id`, `fetched_at`, `as_of`, `normal`, `golden` с
`impact`, `combat_winrate`, `popularity`, `games_with_minion`, `wins`, `losses`)
для страниц «как изменилось». `limit` ≤ 1000 последних снимков.

### `GET /v1/battlegrounds/minions/stats/slices`

Последний снимок каждого сохранённого среза: `snapshot_id`, `fetched_at`,
`list_as_of`, `has_positions`, `minions_total`.

GraphQL: `battlegroundMinionStats(minion, mmrPercentile, timeRange, compositionId)`
возвращает тот же объект, что `data` REST, из зеркала PostgreSQL.

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
| `GET` | `/ops/provider-usage` | Учёт вызовов платных провайдеров парсинга по дням. |
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

### `GET /ops/provider-usage`

Сводка по каждому вызову Decodo, Scrape.do, Firecrawl и Scrapfly из
`data/provider-usage.sqlite3` (хранение 90 дней, сутки в UTC). Вызов считается
успешным, если провайдер вернул 2xx и ответ прошёл проверку содержимого
каскада; иначе он неудачный с кодом причины (`http_429`, `target_403`,
`content_rejected`, `blocked_content`, `timeout`…). В учёт попадают хост и
усечённый SHA-256 адреса, но не сам URL, ключи или тело ответа.

| Parameter | Type | Default | Описание |
| --- | --- | --- | --- |
| `days` | int | `30` | Окно от `1` до `90` суток, включая текущие. |
| `provider` | enum | — | `decodo`, `scrape_do`, `firecrawl` или `scrapfly`. |
| `source_id` | string | — | Только вызовы одного источника. |

Ответ: `totals`, `providers` и `daily` (всего, успешных, неудачных,
`success_rate`, `avg_ms`, `p95_ms`, `bytes`, `cost_units`, `cost_usd`,
`usd_per_1k_success`), `sources`, `failure_reasons` и `pricing`. Стоимость
хранится в единицах провайдера: кредиты Scrape.do из заголовка
`scrape.do-request-cost`, кредиты Firecrawl и Scrapfly из ответа, у Decodo —
один оплачиваемый запрос за доставленный 2xx. Перевод в доллары использует
`HS_DECODO_USD_PER_UNIT`, `HS_SCRAPE_DO_USD_PER_UNIT`,
`HS_FIRECRAWL_USD_PER_UNIT` и `HS_SCRAPFLY_USD_PER_UNIT`; без них берутся
цены по умолчанию (`pricing.*.basis = "default"`).

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
- `hsguru_matchups`
- `hsguru_archetype_analysis`
- `hsguru_mulligan`
- `hsguru_streamers`

### HSReplay

- `hsreplay_mulligan`
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
