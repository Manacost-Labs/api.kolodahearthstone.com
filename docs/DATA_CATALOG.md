# Каталог данных Hearthstone Parses API

Этот документ отвечает на практический вопрос: **какие данные можно получить
из API, какой endpoint использовать и что означают поля ответа**.

Production base URL:

```text
https://api.kolodahearthstone.com
```

Публичные `GET` endpoints не требуют API-ключа. Полная спецификация параметров,
admin/ops endpoints и правила авторизации находятся в [API.md](API.md), а
автоматически сгенерированный реестр источников — в [SOURCES.md](SOURCES.md).

## Быстрый выбор endpoint

| Задача | Рекомендуемый endpoint |
| --- | --- |
| Получить исходный нормализованный набор конкретного источника | `GET /datasets/{source_id}` |
| Узнать все доступные source ID | `GET /v1/sources` |
| Проверить наличие cache и состояние всех источников | `GET /v1/datasets` |
| Найти колоды разных источников | `GET /v1/constructed/decks` |
| Получить актуальные HSReplay-архетипы | `GET /v1/constructed/archetypes` |
| Получить фильтрованный срез меты HSGuru | `GET /v1/hsguru/meta` |
| Получить актуальные HSGuru-архетипы со сборками | `GET /v1/hsguru/archetypes?min_games=50&has_decks=true` |
| Получить каталог архетипов окна Bans | `GET /v1/hsguru/archetypes?period=black_empire_ban` |
| Получить муллиган архетипа или колоды HSGuru | `GET /v1/hsguru/mulligan` (срезы — `/v1/hsguru/mulligan/slices`) |
| Получить мету и колоды текущей Потасовки HSGuru | `GET /v1/hsguru/brawl/meta`, `GET /v1/hsguru/brawl/decks` |
| Получить турниры: сетки, составы, популярность архетипов | `GET /v1/hsguru/esports/tournaments` и `/v1/hsguru/esports/tournaments/{id}` |
| Получить очки HSEsports по сезонам | `GET /v1/hsguru/esports/points` |
| Получить архив Masters Tour | `GET /v1/hsguru/esports/legacy/{table}` |
| Получить BG-героев solo/duos | `GET /v1/battlegrounds/heroes` |
| Получить BG-существ и их историю | `GET /v1/battlegrounds/minions` и `/api/db/bg/minions/*` |
| Получить классы Арены | `GET /v1/arena/classes` |
| Получить малые/большие BG-аксессуары | `GET /api/bg/trinkets` |
| Получить Vicious Syndicate radars | `GET /datasets/vicious_syndicate_radars` |
| Получить Vicious Data Reaper Live | `GET /datasets/vicious_syndicate_live_beta` |
| Проверить, жив ли API | `GET /health` |

## Форматы ответа

### Dataset endpoint

`GET /datasets/{source_id}` возвращает сохранённый snapshot:

```json
{
  "source_id": "hsreplay_cards_legend_1d",
  "fetched_at": "2026-07-12T10:31:35+00:00",
  "backend": "hsreplay_cards_api",
  "content_length": 514780,
  "data": {
    "site": "hsreplay",
    "category": "ranked",
    "schema_validation": {"ok": true, "type": "card_stats"},
    "structured": {
      "type": "card_stats",
      "cards": []
    }
  }
}
```

Главные поля:

| Поле | Значение |
| --- | --- |
| `source_id` | Стабильный идентификатор набора. |
| `fetched_at` | UTC-время опубликованного snapshot. |
| `backend` | Канал, который дал опубликованный результат. |
| `data.structured.type` | Тип нормализованной схемы. |
| `data.structured` | Основные прикладные данные. |
| `data.schema_validation` | Результат проверки структурной схемы. |
| `data.counts` | Технические количества tables/scripts/links и т. п. |

Поля `tables`, `links`, `json_scripts` и `text_preview` сохраняются для
совместимости и диагностики. Для новых интеграций следует использовать
`data.structured` либо типизированные `/v1/*` endpoints.

### API v1

Все `/v1/*` endpoints используют одинаковый конверт:

```json
{
  "data": [],
  "meta": {
    "source_id": "hsreplay_archetypes",
    "fetched_at": "2026-07-12T10:49:52+00:00",
    "stale": false,
    "count": 42,
    "limit": 100,
    "offset": 0
  }
}
```

`meta.count` — количество строк до пагинации, `limit`/`offset` — параметры
текущего запроса, `stale` — признак устаревшего snapshot.

## Constructed: карты, колоды и архетипы

### Статистика карт — `card_stats`

Источники:

| Формат и ранг | Source ID для 1/3/7/14 дней и текущего патча |
| --- | --- |
| Standard, Legend | `hsreplay_cards_legend_1d`, `hsreplay_cards_legend_3d`, `hsreplay_cards_legend_7d`, `hsreplay_cards_legend_14d`, `hsreplay_cards_legend_patch` |
| Standard, Diamond 4–1 | `hsreplay_cards_diamond_4_1_1d`, `hsreplay_cards_diamond_4_1_3d`, `hsreplay_cards_diamond_4_1_7d`, `hsreplay_cards_diamond_4_1_14d`, `hsreplay_cards_diamond_4_1_patch` |
| Standard, Diamond | `hsreplay_cards_diamond_1d`, `hsreplay_cards_diamond_3d`, `hsreplay_cards_diamond_7d`, `hsreplay_cards_diamond_14d`, `hsreplay_cards_diamond_patch` |
| Standard, Platinum | `hsreplay_cards_platinum_1d`, `hsreplay_cards_platinum_3d`, `hsreplay_cards_platinum_7d`, `hsreplay_cards_platinum_14d`, `hsreplay_cards_platinum_patch` |
| Wild, Legend | `hsreplay_cards_wild_legend_1d`, `hsreplay_cards_wild_legend_3d`, `hsreplay_cards_wild_legend_7d`, `hsreplay_cards_wild_legend_14d`, `hsreplay_cards_wild_legend_patch` |
| Wild, Diamond 4–1 | `hsreplay_cards_wild_diamond_4_1_1d`, `hsreplay_cards_wild_diamond_4_1_3d`, `hsreplay_cards_wild_diamond_4_1_7d`, `hsreplay_cards_wild_diamond_4_1_14d`, `hsreplay_cards_wild_diamond_4_1_patch` |
| Wild, Diamond | `hsreplay_cards_wild_diamond_1d`, `hsreplay_cards_wild_diamond_3d`, `hsreplay_cards_wild_diamond_7d`, `hsreplay_cards_wild_diamond_14d`, `hsreplay_cards_wild_diamond_patch` |
| Wild, Platinum | `hsreplay_cards_wild_platinum_1d`, `hsreplay_cards_wild_platinum_3d`, `hsreplay_cards_wild_platinum_7d`, `hsreplay_cards_wild_platinum_14d`, `hsreplay_cards_wild_platinum_patch` |
| `hsreplay_cards_legend_included_winrate` | Standard, Gold, 14 дней, сортировка по included winrate. |
| `hsreplay_cards_legend_included_popularity` | Standard, Gold, 14 дней, сортировка по included popularity. |

Путь к строкам: `data.structured.cards[]`.

Для каждого рангового среза parser передаёт HSReplay `rankRange`
(`LEGEND`, `DIAMOND_FOUR_THROUGH_DIAMOND_ONE`, `DIAMOND` или `PLATINUM`),
`gameType` и соответствующий `timeRange`. Все 40 наборов обновляются одним
четырёхчасовым production timer.

| Поле | Описание |
| --- | --- |
| `id`, `dbfId` | Card ID Hearthstone и числовой DBF ID. |
| `name`, `cardClass`, `cost`, `rarity`, `type` | Метаданные карты. |
| `deck_popularity` | Доля колод, в которые включена карта. |
| `deck_winrate` | Winrate колод, содержащих карту. |
| `avg_copies` | Среднее число копий в колоде. |
| `times_played` | Объём наблюдений/разы, когда карта была сыграна. |
| `winrate_when_drawn` | Winrate игр, где карта была взята. |
| `winrate_when_played` | Winrate игр, где карта была сыграна. |
| `keep_percentage` | Частота оставления на муллигане. |
| `opening_hand_winrate` | Winrate при наличии в стартовой руке. |
| `avg_turns_in_hand` | Среднее время нахождения в руке. |
| `avg_turn_played_on` | Средний ход розыгрыша. |

Фильтры выборки находятся рядом: `game_type`, `rank_range`, `time_range`,
`sort_mode`.

### Meta-архетипы — `hsreplay_meta_archetypes`

Источники:

- `hsreplay_meta_archetypes_legend_eu_1d`
- `hsreplay_meta_top_1000_legend_1d_firecrawl`
- `hsreplay_meta_legend_1d_firecrawl`
- `hsreplay_meta_diamond_4to1_1d_firecrawl`

Путь: `data.structured.classes[]`. Каждая группа содержит `class`, `games` и
`archetypes[]`; у архетипа доступны название, winrate, popularity, games и
идентификаторы. `filters` фиксирует rank/time/region/game type, `as_of` — дату
данных. Эти четыре ежедневных среза публикуются только при
`data.structured.upstream_freshness.status = "fresh"`; статус рассчитывается по
`as_of` и допускает не более 36 часов. В `/v1/sources` и `/v1/datasets` для них
доступны `upstream_freshness` (без сырых HTTP-заголовков) и
`fresh_only_eligible`. Если доказательство отсутствует или срез устарел,
новая публикация блокируется, а `GET /datasets/{source_id}` отвечает `503`,
вместо выдачи старого LKG как актуального.

### База архетипов HSReplay

Source ID: `hsreplay_archetypes` (`kind=pipeline`).

Рекомендуемый endpoint:

```http
GET /v1/constructed/archetypes
```

Фильтры: `class_name`, `q`, `rank_range`, `game_type`, `limit`, `offset`.

Поля архетипа:

- `archetype_id`, `name`, `slug`, `player_class`, `class_name`;
- `win_rate`, `total_games`, `pct_of_total`, `pct_of_class`;
- `tier_position`, `position`, `region`, `rank_range`, `game_type`;
- `fetched_at`, `as_of_popularity`, `url`.

Для одного архетипа доступны дополнительные endpoints:

| Endpoint | Данные |
| --- | --- |
| `/api/db/archetypes/{id}` | Summary, mulligan, matchups, decks и history. |
| `/api/db/archetypes/{id}/mulligan` | Карты стартовой руки: keep rate, played WR, drawn WR и выборка. |
| `/api/db/archetypes/{id}/matchups` | Противник, winrate матча и число игр. |
| `/api/db/archetypes/{id}/decks` | Популярные сборки, deck code и карты. |
| `/api/db/archetypes/{id}/history` | Временной ряд popularity/winrate/games. |

### Поиск колод

`GET /v1/constructed/decks` объединяет SQL-индекс колод. Фильтры:
`class_name`, `format_name`, `source_id`, `min_win_rate`, `q`, `limit`,
`offset`.

Основные поля: `id`, `source_id`, `title`, `archetype`, `class`, `format`,
`deck_code`, `win_rate`, `updated_at`. Дополнительные поля источника сохраняются.

Другие наборы колод:

| Source ID | Данные |
| --- | --- |
| `hsreplay_decks_trending` | `decks[]`: name, winrate, games, duration, deck URL/ID. |
| `hearthstone_decks` | 20 Standard + 20 Wild Legend posts: WordPress post ID, player, rank, score, published/modified timestamps, deck code, статус и provenance его извлечения. |
| `firestone_standard` | Firestone Standard Legend `last-patch`: `decks[]`, `archetypes[]`, выборки, core cards, games, wins и `winrate` как доля `0..1`. |
| `metastats_decks` | Архетип, класс, winrate, games, cards, deck code. |
| `hsguru_streamer_decks_legend_1000` | Streamer, peak/latest rank, win-loss, format, last played, links и deck code. |
| `hsguru_deck_radar` | SQLite история точных deckstring из опубликованных streamer snapshots: baseline без событий, затем `candidate` и `confirmed` после второго независимого наблюдения. Контракт: [HSGURU_DECK_RADAR.md](HSGURU_DECK_RADAR.md). |
| `hsguru_fun_decks` | Off-meta / fun decks: `fun_score`, `max_meta_similarity`, nearest archetype, reasons; derived from streamer candidates vs meta catalogs. |

### Прямые constructed-наборы

`hearthstone_decks` делает два прямых WordPress REST GET: категория `3`
для Standard и `13` для Wild, по 20 постов. После проверки REST-схемы
и декодируемости deck code отсутствующий код может быть взят из
LKG или точечно из detail page. Если REST-набор невалиден, включается
проверяемый HTML cloud-каскад, затем residential fallback. Кандидат содержит
ровно 40 строк и не менее 95% deck codes; иначе остаётся предыдущий
LKG.

`firestone_standard` параллельно делает два прямых CDN GET к ZeroToHeroes:
обзор колод и обзор архетипов Standard, Legend, `last-patch`. Набор
не расходует Scrape.do, Firecrawl, Bright Data, Scrapfly или residential proxy.
Перед публикацией требуются оба среза, минимум 20 строк суммарно,
минимум 10 колод и 10 архетипов, а также schema, semantic, contract и
regression gates. Проверка свежести отклоняет upstream timestamp старше 36
часов или более чем на 6 часов в будущем; сокращение `decks` и `archetypes`
контролируется раздельно. Провал любого gate сохраняет LKG.

```http
GET /datasets/hearthstone_decks
GET /datasets/firestone_standard
```

> `firestone_standard` нельзя включать в публичном или коммерческом production
> без письменного разрешения Firestone/ZeroToHeroes. См.
> [Firestone Terms of Service](https://github.com/Zero-to-Heroes/firestone/blob/master/tos.md).
> Интеграция fail-closed: `HS_FIRESTONE_STANDARD_AUTHORIZED` по умолчанию равен
> `false`. До явного `true` источник не запускается вручную или по расписанию,
> сохранённый ранее snapshot не публикуется через dataset/demo/v1, а источник
> исключён из denominator verified completeness.

## Matchups и meta

| Source ID | Структура и назначение |
| --- | --- |
| `hsguru_meta_standard_legend` | Standard Legend archetypes: winrate, popularity, duration, turns, climbing speed. |
| `hsguru_meta_standard_diamond_4to1` | Standard Diamond 4–1. |
| `hsguru_meta_standard_top_5k` | Standard Top 5K. |
| `hsguru_meta_standard_top_legend` | Standard Top Legend. |
| `hsguru_meta_matrix` | Unified twelve-hourly matrix: Standard/Wild, nine ranks, seven rolling periods (`past_3_hours` … `past_30_days`), the latest patch, the current expansion window (`black_empire`), Any Player and local 100–5000 game thresholds. It also contains the current Standard/Wild archetype catalog (minimum 50 games), cached builds and time-series statistics. |
| `hsguru_meta_wild_legend` | Wild Legend. |
| `hsguru_meta_wild_diamond_4to1` | Wild Diamond 4–1. |
| `hsguru_meta_wild_top_5k` | Wild Top 5K. |
| `hsguru_meta_wild_top_legend` | Wild Top Legend. |
| `hsguru_matchups_legend` | Standard Legend: `matchups[]` with archetype, opponent (`vs`) and winrate; minimum 100 archetype games and 25 matchup games. |
| `hsguru_matchups_wild_legend` | Wild Legend: the same matchup matrix and minimum sample thresholds. |
| `hsguru_matchups_diamond_4to1` | Та же matchup-матрица для Diamond 4–1. |
| `hsguru_archetype_analysis` | Daily checkpointed Standard/Wild archetype analysis: class matchups and card mulligan/drawn/kept impact, with sparse post-patch samples distinguished from unavailable upstream data. |
| `hsguru_mulligan` | Муллиган (mulligan/drawn/not drawn/kept impact и count) и матчапы по классам для архетипов и колод по рангу, периоду (включая баны), монетке и классу соперника. История в SQLite `hsguru_*` и PostgreSQL `analytics.hsguru_*`, см. [HSGURU_MULLIGAN.md](HSGURU_MULLIGAN.md). Dataset хранит только сводку покрытия. |
| `metastats_matchups` | Archetype, opponent, winrate/vs_winrate и games. |

HSGuru meta-строки находятся в `data.structured.strategies[]`, matchup-строки —
в `data.structured.matchups[]`.

Актуальный каталог находится в
`data.structured.current_catalog.archetypes[]`. У строки есть `games`,
`winrate`, `popularity_pct`, `deck_count`, `has_decks` и `decks[]`; каждая
сборка содержит deck code, число игр, winrate, класс и ссылку HSGuru.
`GET /v1/hsguru/archetypes` поддерживает фильтры `format`, `period`,
`min_games`, `has_decks`, поиск, сортировку и пагинацию. Временной ряд доступен
через `GET /v1/hsguru/archetypes/history`.

Каталог окна Bans (`period=black_empire_ban`) лежит в
`data.structured.period_catalogs.black_empire_ban` с той же структурой. Он
строится из среза матрицы ALL/Any Player без отдельного запроса; его сборки
берутся из `hsguru_deck_catalog_{format}_all_black_empire_ban` и не дополняются
сборками из более широких окон, которые могли бы содержать забаненные карты.
`meta.available_periods` перечисляет опубликованные окна. Каталоги содержат
архетипы от 10 игр (значение `min_games` по умолчанию); сборки HSGuru
показывает только от 50 игр, поэтому у архетипов ниже этого порога `decks[]`
всегда пуст. Срезы `GET /v1/hsguru/meta` тоже хранятся от 10 игр и включают
ранг `bronze_plat`; значение `min_games` по умолчанию там остаётся 100.

## HSGuru: Потасовка и киберспорт

Четыре pipeline-набора читают страницы HSGuru через Scrape.do без браузера
(1 кредит за страницу, резидентный прокси — 10 кредитов — только если обычный
запрос не прошёл). Единственная страница с браузером — `/esports`: её таблица
приходит по сокету LiveView. Каждый запуск читает не больше 5 страниц
одновременно: лимит аккаунта Scrape.do — 50, муллиган занимает 40. Страницы
идут как `detail`-задачи общего месячного бюджета HSGuru (Meta-Lab).

| Source ID | Таймер | Что внутри |
| --- | --- | --- |
| `hsguru_brawl` | `refresh-hsguru-brawl`, каждые 3 часа в :50 | Мета текущей Потасовки (`format=-1&period=brawl`): 7 рангов × 3 варианта монетки и 11 классов соперника для всех рангов, от 10 игр; колоды Потасовки (от 50 игр, ранги `all`, `legend`, `diamond_to_legend`). |
| `hsguru_esports_tournaments` | `refresh-hsguru-esports`, ежедневно в 05:40 | Ближайшие турниры `/esports`; все официальные тур-стопы `/mt/tour-stops` (2019–2026) и стартовавшие за 14 дней турниры из `/esports`: standings каждой стадии, составы игроков с кодами колод, популярность архетипов и классов в составах. |
| `hsguru_esports_points` | тот же таймер | Очки HSEsports `/leaderboard/points` за все сезоны (2023 … 2026, China) для Standard и Battlegrounds: место, игрок, регион, страна, очки по месяцам и итог. |
| `hsguru_esports_legacy` | тот же таймер, не чаще раза в неделю | Архив Masters Tour: статистика игроков по MT, статистика квалификаций по годам и тур-стопам, очки и призовые legacy-сезонов. |

### Потасовка — `hsguru_brawl`

Путь: `data.structured.meta[]` и `data.structured.decks[]`.

| Поле среза меты | Описание |
| --- | --- |
| `key` | `rank|coin|opponent_class`, например `all|any_player|any`. |
| `rank` | `all`, `top_legend` (Top 1k), `top_5k`, `legend`, `diamond_4to1`, `diamond_to_legend`, `bronze_plat`. В Потасовке почти все игры попадают только в `all`. |
| `coin` | `any_player`, `on_coin`, `going_first`. |
| `opponent_class` | `any` или класс заглавными буквами. |
| `total_games` | Сумма игр строк среза. |
| `rows[]` | `archetype`, `winrate`, `popularity` (%), `games`, `turns`, `duration_minutes`, `climbing_speed` (звёзд в час). |

Строка колоды: `title`, `archetype`, `class`, `format` (`Brawl`), `deck_code`,
`win_rate`, `games`, `url`, `sample_rank`, `sample_period`.

HSGuru не называет саму Потасовку на странице: набор описывает текущую
Потасовку на момент `fetched_at`. Набор публикуется, только если прочитан срез
`all|any_player|any`; иначе остаётся прошлый снимок (`serving_cached_dataset`).
Матчапы Потасовки — часть срезов матчапов и здесь не собираются.

```http
GET /v1/hsguru/brawl/meta?rank=all&coin=any_player&opponent_class=any&min_games=10
GET /v1/hsguru/brawl/decks?rank=all&class=Priest
```

### Турниры — `hsguru_esports_tournaments`

| Путь | Содержимое |
| --- | --- |
| `data.structured.upcoming[]` | `tournament_id` (Battlefy), `name`, `start_time` (UTC), `tags` (Open/Bo3/регион/формат), `url`. |
| `data.structured.tournaments[]` | `tournament_id`, `name`, `kind` (`tour_stop` или `community`), `start_time`, `battlefy_url`, `standings_url`, `lineups_url`, `stages[]`, `lineups[]`, `lineup_popularity`, `complete`, `collected_at`, `reused`. |
| `stages[]` | `stage_id`, `name` (Swiss, Group A, Top 8 …), `standings[]`: `place`, `player`, `score`, `wins`, `losses`. |
| `lineups[]` | `player` и `decks[]`: `archetype`, `class`, `deck_code`, `url`. |
| `lineup_popularity` | `lineups` (число составов), `archetypes[]` и `classes[]` с `lineups` и `share` (доля составов). |

`player` — объект `name`, `battletag`, `region` (`EU`/`AM`/`AP`/`CN`, если
HSGuru его показывает), `country` (ISO-код флага), `profile_url`.

Завершённый турнир (старт больше 10 дней назад, полный снимок собран позже
этого срока) не читается повторно. Если повторное чтение незавершённого турнира
не удалось, остаётся его последний полный снимок с `refresh_failed: true`.
Винрейты и матчапы архетипов турнира HSGuru считает по сокету («Preparing
stats…»), их в наборе нет; популярность считается из составов.

```http
GET /v1/hsguru/esports/upcoming
GET /v1/hsguru/esports/tournaments?kind=tour_stop&limit=50
GET /v1/hsguru/esports/tournaments/6a6c88f821e3c30013db3d46
```

### Очки HSEsports — `hsguru_esports_points`

`data.structured.seasons[]`: `season` (`2026`, `2026_summer`,
`china_2026_spring` …), `season_label`, `leaderboard` (`STD`, `BG_LL`),
`leaderboard_label`, `headers[]`, `rows[]`. Строка: `rank`, `player`, по
колонке на месяц или турнир сезона (`january` … или имя тур-стопа) и `total`.
Список сезонов берётся со страницы, новые сезоны появятся сами.

```http
GET /v1/hsguru/esports/points?season=2026&leaderboard=STD&region=EU
GET /v1/hsguru/esports/points/seasons
```

Без `season` возвращается последний годовой сезон (как на HSGuru).

### Архив Masters Tour — `hsguru_esports_legacy`

| Путь | Содержимое |
| --- | --- |
| `mt_stats` | Игроки по всем MT: `tour_stops`, `top_8`, `matches_won`, `matches_lost`, `winrate_pct`. |
| `qualifier_stats[]` | По периоду (`all` = 2020–2026, год или тур-стоп): `cups`, `top_8`, `top_16`, `winrate_pct`. |
| `mt_points[]` | Legacy-сезоны (`points_system`, `season`): очки или призовые по турнирам и `total`. |

```http
GET /v1/hsguru/esports/legacy/mt-stats
GET /v1/hsguru/esports/legacy/qualifier-stats?selector=2021
GET /v1/hsguru/esports/legacy/mt-points?selector=2022_fall
```

Все эти endpoint'ы возвращают конверт `/v1` (`data`, `meta`); в `meta` —
`source_id`, `fetched_at`, `stale` по `stale_hours` источника и `count`.
GraphQL: `hsguruBrawlMeta`, `hsguruBrawlDecks`, `hsguruEsportsTournaments`,
`hsguruEsportsPoints`, `hsguruEsportsLegacy` (та же форма, см.
[GRAPHQL_API.md](GRAPHQL_API.md)).

## Аудит покрытия HSGuru

Обход всех разделов hsguru.com 25.10.2026 через Scrape.do (`render=false`,
кроме `/esports`). «Сессия» — тема, которую ведёт отдельная параллельная задача.

| Раздел | Данные | Собираем | Source ID / причина |
| --- | --- | --- | --- |
| `/meta?format=2`, `format=1` | Мета Standard/Wild по рангам, периодам, монетке | да | `hsguru_meta_matrix`, `hsguru_meta_*` |
| `/meta?format=-1&period=brawl` | Мета Потасовки | да | `hsguru_brawl` |
| `/decks?format=2`, `format=1` | Колоды по архетипам | да | `hsguru_deck_catalog_*` |
| `/decks?format=-1&period=brawl` | Колоды Потасовки | да | `hsguru_brawl` |
| `/meta?format=4`, `/decks?format=4` | Twist | нет | Страница существует («Twist Decks»), но данных нет: формат не в селекторе, таблица пуста даже с сессией. Других форматов HSGuru не показывает. |
| `/matchups` | Матрица матчапов | да | `hsguru_matchups_*`; срезы — сессия matchup-slices |
| `/matchups?format=-1&period=brawl` | Матчапы Потасовки | нет | Относится к срезам матчапов (сессия matchup-slices). |
| `/card-stats?archetype=…`, `?deck_id=…` | Муллиган архетипа/колоды | да | `hsguru_mulligan` |
| `/card-stats` без архетипа | Статистика всех карт формата | нет | Закрыта более высоким тарифом Patreon: наша сессия получает «You do not have access to these filters». |
| `/archetype/<name>`, `/deck/<id>` | Матчапы по классам | да | `hsguru_mulligan`, `hsguru_archetype_analysis` |
| `/streamer-decks` | Колоды стримеров | да | `hsguru_streamer_decks_legend_1000`; расширение — сессия streamers |
| `/streaming-now` | Кто стримит сейчас | сессия streamers | — |
| `/replays` | Последние игры с колодами | частично | Фолбэк сборок `hsguru_deck_catalog_*` |
| `/leaderboard?region=…` | Ладдерные таблицы лидеров | сессия leaderboards | Берутся из официального API Blizzard. |
| `/leaderboard/player-stats` | Статистика игроков по таблицам | нет | Требует входа (HTTP 401 без сессии). |
| `/leaderboard/points` | Очки HSEsports | да | `hsguru_esports_points` |
| `/player-profile/<ник>` | Финиши игрока в таблицах лидеров, MT и квалификациях | нет | Страница на игрока; финиши в таблицах — тема сессии leaderboards. |
| `/esports` | Ближайшие и идущие турниры | да | `hsguru_esports_tournaments.upcoming` (рендер, 5 кредитов) |
| `/mt/tour-stops` | Официальные тур-стопы | да | `hsguru_esports_tournaments` |
| `/battlefy/tournament/<id>` | Стадии и standings | да | `hsguru_esports_tournaments` |
| `/battlefy/tournament/<id>/lineups` | Составы игроков | да | `hsguru_esports_tournaments` |
| `/tournament-lineups/battlefy/<id>/…` | Популярность, винрейт и матчапы архетипов турнира | частично | Популярность считаем из составов; винрейт и матчапы приходят по сокету. |
| `/battlefy/tournament/<id>/participants` | Зарегистрированные | нет | Приходит по сокету. |
| `/battlefy/third-party-tournaments` | Турниры сторонних организаторов | частично | Стартовавшие за 14 дней из `/esports` (`kind=community`). |
| `/battlefy/multi-tournament-stats` | Свод нескольких турниров | нет | Форма ввода, без данных. |
| `/legacy-hsesports/mt/stats`, `/mt/qualifier-stats/*`, `/mt/points`, `/mt/earnings` | Архив MT | да | `hsguru_esports_legacy` |
| `/legacy-hsesports/invited`, `/qualifiers` | Приглашённые и квалификации | нет | Пусто с 2026 года. |
| `/cards` | Карточная база | нет | Дублирует каталог карт HearthstoneJSON. |
| `/fantasy` | Фэнтези-лиги | нет | Требует входа. |
| `/deckviewer`, `/deckbuilder`, `/lineup-importer`, `/chat-bot-command-hook/help`, `/discord-bot`, `/hdt-plugin`, `/about`, `/` | Инструменты и служебные страницы | нет | Данных нет. |
| `/stats` | — | нет | HTTP 404. |

## Battlegrounds

### Герои

| Endpoint/source | Что доступно |
| --- | --- |
| `/v1/battlegrounds/heroes?mode=solo` | Пагинированный актуальный список solo-героев. |
| `/v1/battlegrounds/heroes?mode=duos` | Duos tier list. |
| `/api/bg/heroes/{dbfId}` | Подробности одного героя. |
| `hsreplay_battlegrounds_heroes` | Premium tier list snapshot (`bg_heroes`). |
| `hsreplay_battlegrounds_hero_details` | Детальный pipeline snapshot (`bg_hero_details`). |

Основные поля героя: `hero`, `dbfId`, `pick_rate`, `avg_placement`, `tier`,
`placement_distribution`, `best_composition`, `best_composition_id`,
`adjusted_avg_placement`, `anomaly_adjusted`, `detail_available`,
`key_minions_top3`.

Детальные sub-endpoints:

- `/api/bg/heroes/{dbfId}/tavern-up` — результаты улучшения таверны по ходам;
- `/api/bg/heroes/{dbfId}/hero-power` — использование силы героя;
- `/api/bg/heroes/{dbfId}/best-composition` — лучший и альтернативные составы.

### Существа и карты

`GET /v1/battlegrounds/minions` поддерживает `q`, `tavern_tier`, `limit`, `offset`.

Основные поля: `dbf_id`, `card_id`, `name`, `tavern_tier`, `popularity`,
`combat_winrate`, `fetched_at`.

Dataset `hsreplay_battlegrounds_minions` дополнительно содержит `impact`,
`win_share`, `avg_placement_with`, `avg_placement_without`, games with/without
minion и `combat_rounds`.

Полная статистика как на странице существа HSReplay — pipeline
`hsreplay_battlegrounds_minion_stats` (dataset `bg_minion_stats` со сводкой
срезов; история в SQLite `bg_minion_stat_*` и PostgreSQL
`analytics.bg_minion_stat_*`, см. [API.md](API.md#hsreplay-battlegrounds-minion-statistics)):

- `/v1/battlegrounds/minions/stats` — итоги обычной и золотой версии всех существ среза `mmr`/`period`;
- `/v1/battlegrounds/minions/{minion}/stats` — по ходам 1–16, позиции по составу, покупка копий, медианные статы;
- `/v1/battlegrounds/minions/{minion}/stats/history` — итоги по снимкам;
- `/v1/battlegrounds/minions/stats/slices` — сохранённые срезы.

Исторические endpoints:

- `/api/db/bg/minions/{dbfId}` — последний snapshot и combat-round stats;
- `/api/db/bg/minions/{dbfId}/history` — time series popularity/combat WR;
- `/api/db/bg/minions` — поиск и фильтрация snapshot-таблицы.

Firestone-наборы:

| Source ID | Данные |
| --- | --- |
| `firestone_battlegrounds_cards` | Карты/существа по tavern tier и performance metrics. |
| `firestone_battlegrounds_spells` | Заклинания по tier и performance metrics. |

Они используют `type=bg_card_stats`; строки сгруппированы в
`data.structured.tiers`.

### Составы

| Source ID | Тип | Данные |
| --- | --- | --- |
| `hsreplay_battlegrounds_compositions` | `bg_compositions` | composition ID/type, first place, avg placement, popularity, games, распределение мест. |
| `hsreplay_battlegrounds_comps` | `bg_comps` | Название/slug/tier, core/main/additional cards, описание, when to commit, how to play. |
| `hsreplay_battlegrounds_compositions_screenshot` | validated image | Ежедневный screenshot: только PNG/JPEG/WebP с проверенными MIME, сигнатурой и размерами. |
| `firestone_battlegrounds_comps` | `bg_comps` | Альтернативный список составов и ключевых карт. |

Последний screenshot HSReplay compositions доступен через:

- `/api/bg/compositions/screenshot/latest`;
- `/api/bg/compositions/screenshot/latest/image`.

### Аксессуары

Источники `hsreplay_battlegrounds_trinkets_lesser` и
`hsreplay_battlegrounds_trinkets_greater` используют `type=bg_trinkets`.

Дополнительные агрегированные срезы объединяют малые и большие аксессуары:

| MMR | Текущий BG-патч | Последние 7 дней |
| --- | --- | --- |
| Все игроки | `hsreplay_battlegrounds_trinkets_all_current_battlegrounds_patch` | `hsreplay_battlegrounds_trinkets_all_last_7_days` |
| Топ 50% | `hsreplay_battlegrounds_trinkets_top_50_percent_current_battlegrounds_patch` | `hsreplay_battlegrounds_trinkets_top_50_percent_last_7_days` |
| Топ 20% | `hsreplay_battlegrounds_trinkets_top_20_percent_current_battlegrounds_patch` | `hsreplay_battlegrounds_trinkets_top_20_percent_last_7_days` |
| Топ 5% | `hsreplay_battlegrounds_trinkets_top_5_percent_current_battlegrounds_patch` | `hsreplay_battlegrounds_trinkets_top_5_percent_last_7_days` |
| Топ 1% | `hsreplay_battlegrounds_trinkets_top_1_percent_current_battlegrounds_patch` | legacy-наборы `hsreplay_battlegrounds_trinkets_lesser` и `hsreplay_battlegrounds_trinkets_greater` |

Поля: `trinket_id`, `trinket_tier`, `name`, `localized_name`, `dbfId`, `cost`,
`pick_rate`, `avg_placement`, `placement_distribution`, `race`, `tribe`,
`variant_key`, `description`, `guide`.

Объединённый endpoint:

```http
GET /api/bg/trinkets?trinket_tier=all&active_only=true
```

## Arena

| Source ID | Тип | Что можно получить |
| --- | --- | --- |
| `hsreplay_arena` | `arena_class_matrix` | Классы: winrate, pick rate, 7+ wins, drafts; matchup matrix. |
| `hsreplay_arena_class_pages_firecrawl` | `arena_class_pages` | Те же class metrics и provenance каждой class page. |
| `hsreplay_arena_cards_advanced` | `arena_card_tiers` | Карты: class, tier, score, win/pick/offer rate и расширенные card metrics. |
| `hsreplay_arena_winning_decks` | `arena_winning_decks` | Winning runs, record, final deck, package/legendary data, region/player. |
| `hsreplay_arena_legendaries` | `arena_legendary_groups` | Legendary/key card groups, related cards, pick/offer/winrate. |
| `heartharena_tierlist` | `heartharena_tierlist` | Классы и карты HearthArena с tier/score. |
| `firestone_arena_cards_normal` | `arena_card_tiers` | Regular Arena card statistics. |
| `firestone_arena_cards_underground` | `arena_card_tiers` | Underground Arena card statistics. |
| `firestone_arena_legendaries_normal` | `arena_card_tiers` | Regular legendary-only statistics. |
| `firestone_arena_legendaries_underground` | `arena_card_tiers` | Underground legendary-only statistics. |

Типизированный endpoint классов:

```http
GET /v1/arena/classes?source_id=hsreplay_arena_class_pages_firecrawl
```

Поля строки: `class`, `win_rate`, `pick_rate`, `pct_7_plus`, `num_drafts`.

## Vicious Syndicate

### Data Reaper Live

Source ID: `vicious_syndicate_live_beta`, тип `vicious_live`.

Данные:

- `games`, `format`;
- `class_distribution[]`: класс и frequency;
- `deck_distribution[]`: архетип и доля;
- `tier_list[]`: rank bracket и ранжированные decks с winrate;
- `pie_time_range`, `tier_ladder_time_range`, `tier_matchup_time_range`;
- `upstream_state` и `upstream_availability`, когда upstream готов.

После дополнения Vicious может временно отдавать только `Other <Class>`. Такие
placeholder-строки не публикуются как реальные архетипы; предыдущий валидный
snapshot остаётся доступным, а причина видна в status/ops API.

### Data Reaper Radars

Source ID: `vicious_syndicate_radars`, тип `vicious_syndicate_radars`.

Верхнеуровневые поля:

| Поле | Описание |
| --- | --- |
| `issue` | Выпуск фактически опубликованного radar. |
| `latest_report_issue` | Последний Data Reaper report на сайте. |
| `upstream_state` | `ready`, `upstream_stale` или `upstream_publication_pending` в diagnostics/status нового кандидата. |
| `latest_report_url`, `latest_report_published_at` | Provenance последнего report. |
| `total_radars` | Число валидных radar-графов. |
| `classes_summary` | Классы и найденные архетипы. |
| `diagnostics` | Количество discovered/resolved/parsed radar URLs. |

Каждый элемент `radars[]` содержит `class`, `archetype`, `title`, `issue`,
`url`, `radar_url`, `deck_code`, `nodes[]` и `edges[]`. Node описывает карту и
визуальные свойства; edge связывает две карты и может содержать weight/length.

Если report уже новый, а хотя бы один официальный radar ещё не опубликован, API
отдаёт последний **полный** radar как явно устаревший LKG. Status содержит
`upstream_state=upstream_publication_pending`, `failure_reason_code=unavailable`
и ограниченный readiness-снимок; свежим успехом такая попытка не считается.
Пустой, повреждённый, неполный или смешанный по issue граф quality-gate не
пропускает.

## Патчи Hearthstone

`GET /api/patches` возвращает список патчей с версиями, датами, заголовками и
ссылками на Hearthstone Wiki/hs-manacost.ru. `GET /api/patches/{version}`
возвращает один патч по wiki-версии либо версии hs-manacost.ru.

## Состояния, качество и свежесть

Сохранённый `state`:

| State | Значение |
| --- | --- |
| `ok` | Последний кандидат прошёл структурные и semantic gates. |
| `partial` | Допустимый частичный результат. |
| `quality_error` | Ответ получен, но его нельзя публиковать как качественные данные. |
| `fetch_error`, `http_error` | Ошибка транспорта/upstream HTTP. |
| `blocked_by_protection` | Источник заблокировал запрос. |
| `proxy_required` | Для источника требуется рабочий proxy. |
| `timed_out` | Deadline запуска исчерпан; новые операции не стартуют, последний хороший snapshot сохраняется. |
| `never_fetched` | Успешного запуска ещё не было. |

`effective_state=ok_cached` означает, что refresh завершился неудачно, но API
безопасно продолжает отдавать предыдущий валидный snapshot.

### Честная полнота v1

У источников с реализованным source-specific retrieval-контрактом новый
snapshot содержит `completeness_schema_version=1`. К ним относятся, в
частности, `hsreplay_battlegrounds_minions`,
`hsreplay_battlegrounds_compositions`, `hsreplay_arena_cards_advanced`,
`hsreplay_arena_legendaries`, `firestone_standard` и перечисленные ниже
семейства. Схема включает два независимых доказательства:

- `field_availability` у критичных полей отличает реальное значение,
  детерминированно отсутствующую upstream-метрику и необъяснённую потерю;
- `row_retrieval` сверяет число сырых, допустимых и нормализованных строк,
  отдельно учитывая объяснённые и необъяснённые отбрасывания.

Arena Legendaries дополнительно доказывает покрытие `ALL` и всех 11 class
buckets в `row_retrieval.bucket_coverage`. Пустой список конкретного класса
допустим, но отсутствующий/неизвестный bucket, повтор одной пары
`(bucket, package_key)` или fallback только с `ALL` не считается полным
получением.

`quality_score` (`metric_availability_score`) отвечает на вопрос «сколько
метрик заполнено», а `retrieval_completeness_score` — «какая доля ожидаемых
данных достоверно получена или детерминированно отсутствует у upstream».
Порог заполнения строгих v1-наборов применяется к retrieval-доле, поэтому
честный `explained_unavailable` не ломает публикацию; сырой
`metric_availability_score` при этом не повышается и остаётся видимой мерой
реального наличия чисел.
Итоговая retrieval-оценка является минимумом полноты полей и строк. Любая
необъяснённая потеря или противоречие ставит `retrieval_complete=false` и не
проходит публикационный gate. У Arena Legendaries проверяются и верхние группы,
и метрики `winrate`/`score` каждого `by_class` bucket.

Разрешённые причины отсутствия полей:

| Источник | Причины, считающиеся объяснёнными |
| --- | --- |
| HSReplay BG minions | `no_current_patch_aggregates`, `insufficient_current_patch_sample` |
| HSReplay Arena advanced | `no_games_in_window` |
| HSReplay Arena legendaries (`winrate`) | `upstream_unavailable_at_zero_pick_rate` |
| HSReplay Arena legendaries (`score`) | `upstream_score_not_reported` |
| Firestone Standard | `generic_class_bucket_without_observed_deck_cluster` |

Список объяснённых причин потери целой строки пока пуст. Поэтому parser не может
повысить процент произвольным текстом или AI-оценкой: все неразобранные строки
считаются ошибкой. Legacy snapshot без версии продолжает работать безопасно, но
его retrieval-показатели равны `null` («полнота не измерялась»). Явная версия,
отличная от `1`, отклоняется; будущая схема не может незаметно обойти строгий
gate. Rates Arena проверяются как конечные числа `0..100`, package cards и
идентификаторы обязаны иметь корректную форму и быть уникальными. Если после
неудачного refresh отдаётся LKG, основной quality относится к LKG, а качество
отклонённого кандидата находится в `last_refresh_quality`.

Четыре HSReplay v1-набора также публикуют
`population_completeness="unverifiable"`: upstream не сообщает полный размер
популяции, поэтому число возвращённых строк нельзя честно назвать 100% всех
существующих сущностей. Отдельный `upstream_freshness` имеет состояние
`fresh`, `stale` или `unknown`, возраст и ограниченный список доказательств.
Для BG источником времени служит body `as_of` (порог 36 часов), для Arena —
точные выбранные фильтры, `meta_period_id` и `Last-Modified` (порог 6 часов).
HTTP `Date` означает только время ответа и само по себе свежесть данных не
доказывает.

Известный `stale`, неверные фильтры, некорректные даты/metadata, timestamp из
будущего и несовпадение body/header отклоняют новый кандидат и сохраняют LKG.
Если рабочий fallback не предоставляет target headers, состояние честно равно
`unknown` с причиной `missing_last_modified` или
`transport_evidence_unavailable`; такой результат не выдаётся за доказанно
свежий. В месячном `verified_completeness` в числитель попадает только
`retrieval_complete=true` вместе с `upstream_freshness.status="fresh"`.

BG minions дополнительно проверяет физические домены: placements `1..8`,
`impact` как разницу placements в `-7..7`, проценты `0..100`, целые
неотрицательные counts и согласованность placement sums с количеством игр.
BG compositions допускает небольшой post-patch набор (минимум 5 строк), но
строго проверяет уникальные положительные ID, `avg_placement` в `1..8`, восемь
placement buckets с суммой около 100%, проценты `0..100`, неотрицательные games
и глобальную сумму first-place share около 100%.

Агрегат `/v1/system/parsing-reliability` не смешивает доступность LKG с новым
полным получением. Его `complete_fresh / tracked_attempts` — weighted rate по
попыткам, `macro_complete_fresh_rate_pct` — невзвешенное среднее per-source
rates (ненаблюдавшиеся дают 0), а `sources_meeting_target / instrumented_sources`
— отдельный gate по источникам. `instrumented_sources / catalog_sources`,
наблюдаемость инструментированного набора и охват всех parser attempts — три
самостоятельных coverage gates. Полный справочник полей и знаменателей находится
в [API.md](API.md#get-v1systemparsing-reliability).
Доказанное отсутствие нового upstream-артефакта не ухудшает условный parser
SLO, но остаётся плохим исходом в `end_to_end_fresh_rate_pct`; поэтому задержка
источника не маскируется улучшенным процентом парсера.
Recovery-разрез отдельно показывает, какие historical provisional/LKG события
позже завершились fresh, были объяснены доказанной задержкой upstream или всё
ещё не закрыты. Исходный fresh-only процент при этом не пересчитывается назад.

Все operational-источники включаются в completeness-телеметрию сразу. Это не
делает их автоматически полными: до появления source-specific retrieval
evidence их попытки остаются `unknown` и не попадают в `complete_fresh`.
Семейство из 42 источников `hsreplay_cards_*` уже сверяет количество строк
JSON `card_list` с нормализованными картами и проверяет уникальность `dbfId`;
необъяснимая потеря или дубликат отклоняет полноту попытки.
Восемь `hsguru_meta_*` срезов сверяют строки таблиц и уникальные названия
архетипов по той же fail-closed схеме.
Три `hsguru_matchups_*` среза сверяют все ячейки матрицы и уникальность пары
`(archetype, vs)`. Диагональная self-matchup ячейка считается неприменимой,
а явно присутствующая пустая ячейка — сообщением upstream о недостаточной
выборке после патча; оборванная строка или отсутствующая колонка остаётся
необъяснённой потерей.
Одиннадцать `hsreplay_battlegrounds_trinkets_*` срезов сверяют каждую строку
JSON API и уникальность `variant_key`; для двух legacy-срезов отбрасывание
противоположного Lesser/Greater tier является детерминированным, любая другая
потеря остаётся необъяснённой. Часовой
`hsguru_streamer_decks_legend_1000` также сверяет все строки первой таблицы;
малое окно из одной или двух полностью валидных колод допустимо, пустое или
повреждённое окно — нет.
Четыре `hsreplay_meta_*_1d*` среза сверяют все строки JSON-распределения
архетипов и уникальность `archetype_id`; повреждённый class payload,
невалидная строка или необъяснённая потеря не считаются полным получением.

Поле `macro_target_met` вычисляется backend по точным дробям до округления и
является единственным безопасным сигналом прохождения macro-gate для панели.

Для public-клиента рекомендуется:

1. Использовать `data.structured` или `/v1/*`.
2. Проверять `fetched_at`/`meta.stale`.
3. Сохранять `source_id` вместе с полученными данными.
4. Не смешивать проценты разных rank/time/format filters.
5. Учитывать `upstream_state` у Vicious.

## Кэширование и ETag

Публичные `/v1/*`, `/api/*` и `/datasets*` обычно возвращают:

```http
Cache-Control: public, max-age=300, stale-while-revalidate=600
ETag: "..."
```

Пример условного запроса:

```bash
etag=$(curl -sD - -o /tmp/sources.json \
  https://api.kolodahearthstone.com/v1/sources \
  | awk 'tolower($1)=="etag:" {print $2}' | tr -d '\r')

curl -i -H "If-None-Match: ${etag}" \
  https://api.kolodahearthstone.com/v1/sources
```

Если данные не изменились, API отвечает `304 Not Modified` без тела.

## Готовые примеры

Список всех источников HSReplay:

```bash
curl -s 'https://api.kolodahearthstone.com/v1/sources?site=hsreplay' \
  | jq '.data[] | {id, category, dataset_fetched_at}'
```

Топ карт по deck winrate:

```bash
curl -s 'https://api.kolodahearthstone.com/datasets/hsreplay_cards_legend_1d' \
  | jq '.data.structured.cards | sort_by(.deck_winrate) | reverse | .[:20]'
```

Standard Legend meta HSGuru:

```bash
curl -s 'https://api.kolodahearthstone.com/datasets/hsguru_meta_standard_legend' \
  | jq '.data.structured.strategies[:20]'
```

Solo BG-герои tier A:

```bash
curl -s 'https://api.kolodahearthstone.com/v1/battlegrounds/heroes?mode=solo&limit=500' \
  | jq '[.data[] | select(.tier == "A")]'
```

Существа шестой таверны:

```bash
curl -s 'https://api.kolodahearthstone.com/v1/battlegrounds/minions?tavern_tier=6&limit=500' \
  | jq '.data'
```

Классы Арены:

```bash
curl -s 'https://api.kolodahearthstone.com/v1/arena/classes' \
  | jq '.data | sort_by(.win_rate) | reverse'
```

Последний доступный Vicious radar:

```bash
curl -s 'https://api.kolodahearthstone.com/datasets/vicious_syndicate_radars' \
  | jq '.data.structured | {
      issue,
      latest_report_issue,
      upstream_state,
      total_radars,
      radars: [.radars[] | {class, archetype, nodes: (.nodes|length), edges: (.edges|length)}]
    }'
```

Поиск колод Mage:

```bash
curl -s 'https://api.kolodahearthstone.com/v1/constructed/decks?class_name=Mage&limit=50' \
  | jq '{meta, decks: .data}'
```

## Полный список source ID

Авторитетный актуальный список с site/category/kind/stale policy находится в
[SOURCES.md](SOURCES.md). Этот файл генерируется из кода и проверяется в CI, то
есть не может незаметно разойтись с production registry.
