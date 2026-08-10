# Каталог данных Hearthstone Parses API

Этот документ отвечает на практический вопрос: **какие данные можно получить
из API, какой endpoint использовать и что означают поля ответа**.

Production base URL:

```text
https://api.hs-manacost.ru
```

Публичные `GET` endpoints не требуют API-ключа. Полная спецификация параметров,
admin/ops endpoints и правила авторизации находятся в [API.md](API.md), а
автоматически сгенерированный реестр источников — в [SOURCES.md](SOURCES.md).

## Быстрый выбор endpoint

| Задача | Рекомендуемый endpoint |
| --- | --- |
| Получить исходный нормализованный набор конкретного источника | `GET /datasets/{source_id}` |
| Узнать все доступные source ID | `GET /v1/system/sources` |
| Проверить наличие cache и состояние всех источников | `GET /v1/system/datasets` |
| Найти колоды разных источников | `GET /v1/constructed/decks` |
| Получить актуальные HSReplay-архетипы | `GET /v1/constructed/archetypes` |
| Получить фильтрованный срез меты HSGuru | `GET /v1/hsguru/meta` |
| Получить актуальные HSGuru-архетипы со сборками | `GET /v1/hsguru/archetypes?min_games=50&has_decks=true` |
| Получить BG-героев solo/duos | `GET /v1/bg/heroes` |
| Получить BG-существ и их историю | `GET /v1/bg/minions` и `/api/db/bg/minions/*` |
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
данных.

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
| `hearthstone_decks` | Standard/Wild Legend posts: player, rank, score, date, deck code и статус его извлечения. |
| `metastats_decks` | Архетип, класс, winrate, games, cards, deck code. |
| `hsguru_streamer_decks_legend_1000` | Streamer, peak/latest rank, win-loss, format, last played, links и deck code. |
| `hsguru_fun_decks` | Off-meta / fun decks: `fun_score`, `max_meta_similarity`, nearest archetype, reasons; derived from streamer candidates vs meta catalogs. |

## Matchups и meta

| Source ID | Структура и назначение |
| --- | --- |
| `hsguru_meta_standard_legend` | Standard Legend archetypes: winrate, popularity, duration, turns, climbing speed. |
| `hsguru_meta_standard_diamond_4to1` | Standard Diamond 4–1. |
| `hsguru_meta_standard_top_5k` | Standard Top 5K. |
| `hsguru_meta_standard_top_legend` | Standard Top Legend. |
| `hsguru_meta_matrix` | Unified twelve-hourly Firecrawl matrix: Standard/Wild, nine ranks, five rolling periods, Any Player and local 100–5000 game thresholds. It also contains the current-patch Standard/Wild archetype catalog (minimum 50 games), cached builds and time-series statistics. |
| `hsguru_meta_wild_legend` | Wild Legend. |
| `hsguru_meta_wild_diamond_4to1` | Wild Diamond 4–1. |
| `hsguru_meta_wild_top_5k` | Wild Top 5K. |
| `hsguru_meta_wild_top_legend` | Wild Top Legend. |
| `hsguru_matchups_legend` | Standard Legend: `matchups[]` with archetype, opponent (`vs`) and winrate; minimum 100 archetype games and 25 matchup games. |
| `hsguru_matchups_wild_legend` | Wild Legend: the same matchup matrix and minimum sample thresholds. |
| `hsguru_matchups_diamond_4to1` | Та же matchup-матрица для Diamond 4–1. |
| `metastats_matchups` | Archetype, opponent, winrate/vs_winrate и games. |

HSGuru meta-строки находятся в `data.structured.strategies[]`, matchup-строки —
в `data.structured.matchups[]`.

Актуальный каталог находится в
`data.structured.current_catalog.archetypes[]`. У строки есть `games`,
`winrate`, `popularity_pct`, `deck_count`, `has_decks` и `decks[]`; каждая
сборка содержит deck code, число игр, winrate, класс и ссылку HSGuru.
`GET /v1/hsguru/archetypes` поддерживает фильтры `format`, `min_games`,
`has_decks`, поиск, сортировку и пагинацию. Временной ряд доступен через
`GET /v1/hsguru/archetypes/history`.

## Battlegrounds

### Герои

| Endpoint/source | Что доступно |
| --- | --- |
| `/v1/bg/heroes?mode=solo` | Пагинированный актуальный список solo-героев. |
| `/v1/bg/heroes?mode=duos` | Duos tier list. |
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

`GET /v1/bg/minions` поддерживает `q`, `tavern_tier`, `limit`, `offset`.

Основные поля: `dbf_id`, `card_id`, `name`, `tavern_tier`, `popularity`,
`combat_winrate`, `fetched_at`.

Dataset `hsreplay_battlegrounds_minions` дополнительно содержит `impact`,
`win_share`, `avg_placement_with`, `avg_placement_without`, games with/without
minion и `combat_rounds`.

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
| `upstream_state` | `ready` или `upstream_stale`. |
| `latest_report_url`, `latest_report_published_at` | Provenance последнего report. |
| `total_radars` | Число валидных radar-графов. |
| `classes_summary` | Классы и найденные архетипы. |
| `diagnostics` | Количество discovered/resolved/parsed radar URLs. |

Каждый элемент `radars[]` содержит `class`, `archetype`, `title`, `issue`,
`url`, `radar_url`, `deck_code`, `nodes[]` и `edges[]`. Node описывает карту и
визуальные свойства; edge связывает две карты и может содержать weight/length.

Если report уже новый, а соответствующий radar ещё не опубликован, API отдаёт
последний **полный** radar с `upstream_state=upstream_stale`. Пустой или
повреждённый граф quality-gate не пропускает.

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
  https://api.hs-manacost.ru/v1/system/sources \
  | awk 'tolower($1)=="etag:" {print $2}' | tr -d '\r')

curl -i -H "If-None-Match: ${etag}" \
  https://api.hs-manacost.ru/v1/system/sources
```

Если данные не изменились, API отвечает `304 Not Modified` без тела.

## Готовые примеры

Список всех источников HSReplay:

```bash
curl -s 'https://api.hs-manacost.ru/v1/system/sources?site=hsreplay' \
  | jq '.data[] | {id, category, dataset_fetched_at}'
```

Топ карт по deck winrate:

```bash
curl -s 'https://api.hs-manacost.ru/datasets/hsreplay_cards_legend_1d' \
  | jq '.data.structured.cards | sort_by(.deck_winrate) | reverse | .[:20]'
```

Standard Legend meta HSGuru:

```bash
curl -s 'https://api.hs-manacost.ru/datasets/hsguru_meta_standard_legend' \
  | jq '.data.structured.strategies[:20]'
```

Solo BG-герои tier A:

```bash
curl -s 'https://api.hs-manacost.ru/v1/bg/heroes?mode=solo&limit=500' \
  | jq '[.data[] | select(.tier == "A")]'
```

Существа шестой таверны:

```bash
curl -s 'https://api.hs-manacost.ru/v1/bg/minions?tavern_tier=6&limit=500' \
  | jq '.data'
```

Классы Арены:

```bash
curl -s 'https://api.hs-manacost.ru/v1/arena/classes' \
  | jq '.data | sort_by(.win_rate) | reverse'
```

Последний доступный Vicious radar:

```bash
curl -s 'https://api.hs-manacost.ru/datasets/vicious_syndicate_radars' \
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
curl -s 'https://api.hs-manacost.ru/v1/constructed/decks?class_name=Mage&limit=50' \
  | jq '{meta, decks: .data}'
```

## Полный список 46 source ID

Авторитетный актуальный список с site/category/kind/stale policy находится в
[SOURCES.md](SOURCES.md). Этот файл генерируется из кода и проверяется в CI, то
есть не может незаметно разойтись с production registry.
