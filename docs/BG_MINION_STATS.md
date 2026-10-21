# Статистика существ Полей сражений (HSReplay)

Полная статистика каждого существа Полей сражений из аналитики HSReplay:
итоги обычной и золотой версии, значения по ходам 1–16, позиции на доске,
частота покупки копий и медианные атака/здоровье. Это те же данные, что на
странице существа на сайте, например
<https://hsreplay.net/ru/battlegrounds/minions/104551/>. Значения совпадают с
сайтом до знака: формулы взяты из кода страницы HSReplay и проверены на dbf
104551 21.10.2026.

Короткий справочник по всем маршрутам API — в [API.md](API.md), GraphQL — в
[GRAPHQL_API.md](GRAPHQL_API.md).

## Содержание

- [Как устроено](#как-устроено)
- [Срезы](#срезы)
- [Общие правила](#общие-правила)
- [GET /v1/battlegrounds/minions/{minion}/stats](#get-v1battlegroundsminionsminionstats)
- [GET /v1/battlegrounds/minions/stats](#get-v1battlegroundsminionsstats)
- [GET /v1/battlegrounds/minions/{minion}/stats/history](#get-v1battlegroundsminionsminionstatshistory)
- [GET /v1/battlegrounds/minions/stats/slices](#get-v1battlegroundsminionsstatsslices)
- [GraphQL](#graphql)
- [Формулы](#формулы)
- [Ошибки](#ошибки)
- [Хранение, обновление и ограничения](#хранение-обновление-и-ограничения)

## Как устроено

Страница существа на HSReplay строится из четырёх аналитических запросов.
Каждый запрос отдаёт сразу **все** существа среза (около 272), поэтому один
срез — это четыре запроса, а не по запросу на существо.

| Запрос HSReplay | Что даёт | Объём |
| --- | --- | --- |
| `battlegrounds_minion_list` | Итоги и значения по ходам 1–16 для обычной и золотой версии | ~1,2 МБ |
| `battlegrounds_minion_position` | Позиции 1–7 на доске, обычная доска и доска с провокацией, по составам | ~24–31 МБ |
| `battlegrounds_purchase_rates_by_turn` | Сколько раз существо предлагали и брали при 0, 1, 2 копиях, в том числе через «Раскопку» | ~3 МБ |
| `battlegrounds_minion_stats` | Медианные атака и здоровье по ходам | ~0,7 МБ |

Источник в реестре: `hsreplay_battlegrounds_minion_stats` (pipeline). Сбор
идёт через общий клиент HSReplay с Premium-сессией. Ответ HSReplay `202 Query is
processing` клиент дожидается сам. Каждый успешный прогон сохраняется снимком
(`snapshot_id`) в SQLite платформы и в PostgreSQL `hs_data` (таблицы
`analytics.bg_minion_stat_*`). Имена, картинки и типы существ берутся из нашего
каталога `catalog.battlegrounds_cards` по dbf.

## Срезы

Срез — пара «рейтинг × период». HSReplay отдаёт эти запросы только для трёх
значений рейтинга; на `TOP_5_PERCENT` и `TOP_1_PERCENT` он отвечает «not
supported».

| `mmr` | Что значит |
| --- | --- |
| `TOP_50_PERCENT` | Верхние 50% игроков по MMR. **По умолчанию.** |
| `TOP_20_PERCENT` | Верхние 20% |
| `ALL` | Все игроки |

| `period` | Что значит |
| --- | --- |
| `LAST_7_DAYS` | Последние 7 дней. **По умолчанию.** |
| `CURRENT_BATTLEGROUNDS_PATCH` | Текущий патч Полей сражений |

Сохраняется 6 срезов (3 × 2). Позиции на доске (самый тяжёлый запрос)
собираются только для `TOP_50_PERCENT`. В остальных срезах поле `positions`
равно `null`, а в `/stats/slices` у них `has_positions: false`.

## Общие правила

- Все маршруты публичные, только чтение, ключ API не нужен.
- Ответы кэшируются: `Cache-Control: public, max-age=300, stale-while-revalidate=600`.
  Деградированный ответ без каталога (`images: null`) приходит с `no-store`.
- Конверт ответа общий для API: `{"data": …, "meta": {"source_id", "fetched_at", "stale", "count", …}}`.
- Числа — проценты в диапазоне 0–100 (`combat_winrate`, `popularity`,
  `first_copy` и т. п.), влияние — в местах (`impact`).
- **`null` означает «мало данных»**: так же, как HSReplay, значение скрывается,
  если выборка меньше порога (30 игр или боёв, 30 предложений для покупок,
  200 появлений для позиции). Сырые счётчики при этом отдаются рядом, клиент
  может применить свой порог.
- `minion` в пути — dbf (`104551`) или card id (`BG28_300`). Золотой card id
  (`BG28_300_G`) или золотой dbf приводятся к обычному существу.

## GET /v1/battlegrounds/minions/{minion}/stats

Вся страница одного существа.

### Параметры

| Параметр | Тип | По умолчанию | Описание |
| --- | --- | --- | --- |
| `minion` (путь) | строка | — | dbf или card id |
| `mmr` | строка | `TOP_50_PERCENT` | Рейтинг, см. [Срезы](#срезы) |
| `period` | строка | `LAST_7_DAYS` | Период, см. [Срезы](#срезы) |
| `composition` | целое 0–10000 | `0` | Позиции только для этого состава. `0` — все составы вместе, как «Все составы» на сайте |
| `snapshot_id` | целое ≥ 1 | последний | Прочитать старый снимок того же среза |

### Пример

```bash
curl 'https://api.kolodahearthstone.com/v1/battlegrounds/minions/BG28_300/stats?mmr=TOP_50_PERCENT&period=LAST_7_DAYS'
```

```json
{
  "data": {
    "minion": {
      "dbf_id": 104551, "card_id": "BG28_300", "golden_dbf_id": 104554,
      "name": "Harmless Bonehead", "name_ru": "Безобидная черепушка",
      "tavern_tier": 1, "minion_types": ["UNDEAD"], "mechanics": ["DEATHRATTLE"],
      "attack": 1, "health": 1, "catalog": true, "in_pool": true,
      "images": {
        "card": "https://api.kolodahearthstone.com/uploads/framed/BG28_300.png",
        "card_golden": "https://api.kolodahearthstone.com/uploads/framed/BG28_300_G.png",
        "art": "https://api.kolodahearthstone.com/uploads/art/BG28_300.jpg",
        "horizontal": "https://api.kolodahearthstone.com/uploads/horizontal-art/r8/battleground_card/BG28_300.webp?v=…"
      }
    },
    "slice": {
      "snapshot_id": 1, "mmr_percentile": "TOP_50_PERCENT", "time_range": "LAST_7_DAYS",
      "fetched_at": "2026-10-21T16:51:13+00:00",
      "as_of": {
        "minion_list": "2026-10-20T21:02:05Z", "positions": "2026-10-21T13:41:22Z",
        "purchases": "2026-10-21T13:41:24Z", "medians": "2026-10-21T07:17:54Z"
      }
    },
    "normal": {
      "impact": -0.0144, "combat_winrate": 56.83, "popularity": 1.885,
      "games_with_minion": 18161, "games_without_minion": 974046,
      "avg_placement_with": 3.9514, "avg_placement_without": 3.651,
      "wins": 16625, "losses": 12629
    },
    "golden": {
      "impact": -0.0874, "combat_winrate": 51.724, "popularity": 0.217,
      "games_with_minion": 1570, "games_without_minion": 1713896,
      "avg_placement_with": 4.3752, "avg_placement_without": 4.2504,
      "wins": 1815, "losses": 1694
    },
    "rounds": [
      {
        "combat_round": 7,
        "normal": null,
        "golden": {
          "impact": -0.2147, "combat_winrate": 48.919, "popularity": 0.32,
          "games_with_minion": 319, "games_without_minion": 229019,
          "distinct_games": 198702, "wins": 362, "losses": 378, "at_least_one": 0.32
        }
      }
    ],
    "positions": {
      "composition_id": 0, "default_board": "normal",
      "normal": {
        "occurrences": 50688,
        "positions": [
          {"position": 1, "popularity": 6.739, "impact": -0.2602, "occurrences": 3416, "sum_placement": 21132}
        ]
      },
      "taunt": {"occurrences": 1687, "positions": ["…"]},
      "compositions": [{"composition_id": 2, "name": "Мурлоки"}, {"composition_id": 3, "name": "Звери"}, "…"]
    },
    "purchases": [
      {
        "recruitment_round": 7,
        "first_copy": 1.858, "second_copy": 17.06, "third_copy": 84.545,
        "discover": {"first_copy": 14.338, "second_copy": 19.037, "third_copy": 42.222},
        "counts": {"times_offered_0_owned": 30516, "times_picked_0_owned": 567, "times_offered_1_owned": 1694, "times_picked_1_owned": 289, "times_offered_2_owned": 550, "times_picked_2_owned": 465, "…": 0}
      }
    ],
    "medians": [
      {"combat_round": 16, "normal": {"attack": 8.0, "health": 1.0}, "golden": {"attack": 67.0, "health": 21.0}}
    ],
    "methodology": {"formula_source": "hsreplay.net minion page bundle, verified 2026-10-21 on dbf 104551", "…": "…"}
  },
  "meta": {"source_id": "hsreplay_battlegrounds_minion_stats", "fetched_at": "2026-10-21T16:51:13+00:00", "stale": false, "count": 1}
}
```

Массивы в примере сокращены: `rounds` и `medians` содержат ходы 1–16,
`positions.*.positions` — 7 позиций, `purchases` — все ходы набора.

### Поля ответа

**`minion`** — карточка из нашего каталога.

| Поле | Описание |
| --- | --- |
| `dbf_id`, `card_id` | Идентификаторы обычной версии |
| `golden_dbf_id` | dbf золотой версии |
| `name`, `name_ru` | Название на английском и русском |
| `tavern_tier` | Уровень таверны |
| `minion_types` | Типы существа (`UNDEAD`, `BEAST`, …) |
| `mechanics` | Механики (`DEATHRATTLE`, `TAUNT`, …) |
| `attack`, `health` | Базовые атака и здоровье |
| `in_pool` | Есть ли существо в текущем пуле таверны |
| `catalog` | `false`, если существа нет в каталоге: тогда имена берутся из HearthstoneJSON |
| `images.card`, `images.card_golden` | Портрет в рамке, обычный и золотой |
| `images.art` | Квадратный арт |
| `images.horizontal` | Горизонтальный арт 5:1 для плиток |

**`slice`** — какой снимок отдан.

| Поле | Описание |
| --- | --- |
| `snapshot_id` | Номер снимка (для `?snapshot_id=` и истории) |
| `mmr_percentile`, `time_range` | Срез |
| `fetched_at` | Когда мы забрали данные |
| `as_of.*` | На какой момент HSReplay посчитал каждый из четырёх запросов |

**`normal` / `golden`** — итоги версии существа (шапка страницы на сайте).

| Поле | Описание |
| --- | --- |
| `impact` | Влияние на итоговое место. Плюс — с существом игроки занимают места лучше, минус — хуже |
| `combat_winrate` | Доля выигранных боёв с существом на доске, % |
| `popularity` | Доля игроков, у которых существо есть на доске, % |
| `games_with_minion`, `games_without_minion` | Игры с существом и без него (сумма по ходам) |
| `avg_placement_with`, `avg_placement_without` | Среднее итоговое место с существом и без него |
| `wins`, `losses` | Выигранные и проигранные бои |

**`rounds[]`** — значения по ходам (бой номер `combat_round`, 1–16). Для
каждого хода `normal` и `golden` либо `null` (данных нет), либо:

| Поле | Описание |
| --- | --- |
| `impact` | Влияние на итоговое место, если существо на доске в этом бою |
| `combat_winrate` | Доля выигранных боёв на этом ходу, % |
| `popularity` | Доля игроков с существом на этом ходу, % (= `at_least_one`) |
| `games_with_minion`, `games_without_minion` | Игры с существом и без |
| `distinct_games` | Сколько всего игр дошло до этого хода |
| `wins`, `losses` | Бои на этом ходу |
| `at_least_one` | Как отдаёт HSReplay: % игроков, у которых есть хотя бы одна копия |

**`positions`** — «Где размещать». Только в срезах с позициями
(`TOP_50_PERCENT`), иначе `null`.

| Поле | Описание |
| --- | --- |
| `composition_id` | Выбранный состав, `0` — все составы |
| `default_board` | Какую доску показывать по умолчанию (`normal`) |
| `normal`, `taunt` | Обычная доска и доска, где у существа есть провокация |
| `*.occurrences` | Сколько раз существо стояло на доске |
| `*.positions[].position` | Позиция 1–7 слева направо |
| `*.positions[].popularity` | Доля появлений на этой позиции, % |
| `*.positions[].impact` | Насколько эта позиция лучше остальных: среднее место на других позициях минус среднее место на этой. Плюс — позиция лучше |
| `*.positions[].occurrences`, `sum_placement` | Сырые счётчики |
| `compositions[]` | Составы, для которых есть данные: `composition_id` и `name`. Их можно передать в `?composition=` |

**`purchases[]`** — «Частота покупки» по ходам набора (`recruitment_round`).

| Поле | Описание |
| --- | --- |
| `first_copy` | Как часто берут существо из таверны, когда копий ещё нет, % |
| `second_copy` | Когда уже есть одна копия, % |
| `third_copy` | Когда есть две копии (сбор золотой), % |
| `discover.*` | То же самое для выбора через «Раскопку» |
| `counts` | Сырые счётчики: `times_offered_{0,1,2}_owned`, `times_picked_{0,1,2}_owned`, `times_discover_offered_*`, `times_discover_picked_*`, `total_times_offered`, `total_times_discover_offered` |

Подсказка из интерфейса HSReplay: если вторую копию берут чаще, чем третью,
сбор золотой, скорее всего, ошибка.

**`medians[]`** — «Средние атака/здоровье» по ходам: `normal` и `golden`
с полями `attack` и `health` (медианы, как отдаёт HSReplay), либо `null`.

**`methodology`** — формулы, по которым посчитаны значения, в машиночитаемом
виде. Совпадает с разделом [Формулы](#формулы).

## GET /v1/battlegrounds/minions/stats

Сводка по всем существам последнего снимка среза: таблица «все существа» без
детализации по ходам.

| Параметр | Тип | По умолчанию | Описание |
| --- | --- | --- | --- |
| `mmr` | строка | `TOP_50_PERCENT` | Рейтинг |
| `period` | строка | `LAST_7_DAYS` | Период |
| `tavern_tier` | целое 1–7 | — | Только существа этого уровня таверны |
| `sort` | строка | `popularity` | `popularity`, `impact`, `combat_winrate`, `games_with_minion` — по обычной версии, по убыванию, пустые значения в конце; `tavern_tier` — по возрастанию |
| `limit` | целое 1–500 | `100` | Размер страницы |
| `offset` | целое 0–10000 | `0` | Смещение |

```bash
curl 'https://api.kolodahearthstone.com/v1/battlegrounds/minions/stats?sort=impact&tavern_tier=6&limit=10'
```

Каждая строка `data[]`: `dbf_id`, `card_id`, `name`, `name_ru`, `tavern_tier`,
`images`, `normal`, `golden` (поля как в итогах выше). `meta.count` — сколько
существ подходит под фильтр (например, 34 существа шестого уровня), для пагинации.

```json
{
  "data": [
    {
      "dbf_id": 116195, "card_id": "BG31_323", "name": "Turbo Hogrider", "name_ru": "Свиногонщик",
      "tavern_tier": 6, "images": {"card": "…", "card_golden": "…", "art": "…", "horizontal": "…"},
      "normal": {"impact": 0.3808, "combat_winrate": 67.665, "popularity": 4.359, "games_with_minion": 24359, "…": "…"},
      "golden": {"…": "…"}
    }
  ],
  "meta": {"source_id": "hsreplay_battlegrounds_minion_stats", "count": 34, "limit": 10, "offset": 0, "stale": false}
}
```

## GET /v1/battlegrounds/minions/{minion}/stats/history

Итоги существа по всем сохранённым снимкам среза. Подходит для графиков и
страниц «как изменилось после патча».

| Параметр | Тип | По умолчанию | Описание |
| --- | --- | --- | --- |
| `minion` (путь) | строка | — | dbf или card id |
| `mmr` | строка | `TOP_50_PERCENT` | Рейтинг |
| `period` | строка | `LAST_7_DAYS` | Период |
| `limit` | целое 1–1000 | `90` | Сколько последних снимков вернуть |

```json
{
  "data": {
    "dbf_id": 104551, "mmr_percentile": "TOP_50_PERCENT", "time_range": "LAST_7_DAYS",
    "points": [
      {
        "snapshot_id": 1, "fetched_at": "2026-10-21T16:51:13+00:00", "as_of": "2026-10-20T21:02:05Z",
        "tavern_tier": 1,
        "normal": {"impact": -0.0144, "combat_winrate": 56.83, "popularity": 1.885, "games_with_minion": 18161, "wins": 16625, "losses": 12629},
        "golden": {"impact": -0.0874, "combat_winrate": 51.724, "popularity": 0.217, "games_with_minion": 1570, "wins": 1815, "losses": 1694}
      }
    ]
  },
  "meta": {"source_id": "hsreplay_battlegrounds_minion_stats", "stale": false}
}
```

`tavern_tier` в точке позволяет заметить, что существо переехало на другой
уровень таверны. Новый снимок пишется, только если HSReplay обновил данные
(`as_of` изменился).

## GET /v1/battlegrounds/minions/stats/slices

Какие срезы сохранены и насколько они свежие. Параметров нет.

```json
{
  "data": [
    {
      "mmr_percentile": "TOP_50_PERCENT", "time_range": "LAST_7_DAYS", "snapshot_id": 1,
      "fetched_at": "2026-10-21T16:51:13+00:00", "list_as_of": "2026-10-20T21:02:05Z",
      "has_positions": true, "minions_total": 272
    }
  ]
}
```

| Поле | Описание |
| --- | --- |
| `snapshot_id` | Последний снимок среза |
| `fetched_at` | Когда мы его забрали |
| `list_as_of` | Дата расчёта HSReplay для итогов и ходов |
| `has_positions` | Есть ли в срезе позиции на доске |
| `minions_total` | Сколько существ в срезе |

## GraphQL

`battlegroundMinionStats(minion, mmrPercentile, timeRange, compositionId)`
возвращает JSON того же вида, что `data` у
`/v1/battlegrounds/minions/{minion}/stats`. Данные берутся из зеркала PostgreSQL,
поэтому появляются там после ближайшей синхронизации (каждые 15 минут).
PostgreSQL хранит числа как `real`: последний знак может отличаться от REST.

```bash
curl -X POST https://api.kolodahearthstone.com/v1/graphql \
  -H 'Content-Type: application/json' \
  -d '{"query":"{ battlegroundMinionStats(minion: \"BG28_300\", mmrPercentile: \"TOP_50_PERCENT\", timeRange: \"LAST_7_DAYS\") }"}'
```

## Формулы

Формулы повторяют код страницы HSReplay. Каждый ход учитывается с весом
`distinct_games / pct_players_remaining[ход]`, где `pct_players_remaining` —
доля игроков, ещё живых к этому ходу (HSReplay отдаёт её в метаданных запроса).

| Значение | Формула | Скрывается (`null`), если |
| --- | --- | --- |
| Итоговое `impact` | Σ по ходам с существом `(avg_place_without − avg_place_with) × games_with / pct_players_remaining`, делённое на Σ `games_with / pct_players_remaining` | выборка < 30 |
| Итоговое `combat_winrate` | `100 × wins / (wins + losses)` | боёв < 30 |
| Итоговое `popularity` | Σ `at_least_one × вес хода` / Σ `вес хода` | выборка < 30 |
| `impact` хода | `avg_place_without − avg_place_with` | игр с существом или без < 30 |
| `combat_winrate` хода | `100 × wins / (wins + losses)` | боёв < 30 |
| `popularity` хода | `at_least_one` | игр с существом или без < 30 |
| Покупка k-й копии | `100 × times_picked_{k−1}_owned / times_offered_{k−1}_owned` | предложений ≤ 30 |
| `popularity` позиции | `100 × появления на позиции / все появления выбранных строк` (состав `0` суммирует все строки существа) | — |
| `impact` позиции | среднее место на остальных позициях − среднее место на этой | появлений < 200 |
| Медианы | `median_attack`, `median_health` как есть | нет данных |

`avg_place_with = sum_of_placements_for_players_with_minion / count_of_games_with_minion`,
`avg_place_without` — так же для игр без существа.

## Ошибки

| Код | Когда | Пример тела |
| --- | --- | --- |
| `404` | Неизвестное существо или нет снимка для среза | `{"detail": "No statistics stored for this minion and slice"}` |
| `404` | `snapshot_id`, детали которого уже удалены ретенцией (итоги есть в `/stats/history`) | — |
| `422` | Неподдерживаемый рейтинг | `{"detail": "Unsupported mmr: TOP_5_PERCENT; expected one of ALL, TOP_50_PERCENT, TOP_20_PERCENT"}` |
| `422` | Неподдерживаемый период | `{"detail": "Unsupported period: FOO; expected one of LAST_7_DAYS, CURRENT_BATTLEGROUNDS_PATCH"}` |
| `422` | Параметр вне диапазона (`limit`, `offset`, `tavern_tier`, `composition`) | стандартная ошибка валидации FastAPI |

## Хранение, обновление и ограничения

- **Расписание:** `hs-data-api-docker-refresh-bg-minion-stats.timer`, раз в
  сутки в 16:20 по Варшаве (`Persistent=true`), запись в журнале расписаний.
  Если `as_of` у HSReplay не изменился, новый снимок не пишется.
- **Защита данных:** срез, не прошедший проверку схемы, объёма или защиту от
  регрессии, не публикуется; API продолжает отдавать прошлый снимок. Если пул
  существ после ротации законно сократился больше чем на 35%, разовый прогон
  с `HS_BG_MINION_STATS_ALLOW_POOL_DROP=1` снимает защиту.
- **Ретенция:** снимки и итоги хранятся бессрочно, значения по ходам — 60 дней,
  позиции, покупки и медианы — 30 дней. Последний снимок каждого среза не
  удаляется никогда. Сроки меняются переменными `HS_BG_MINION_STATS_*_RETENTION_DAYS`.
- **Стоимость:** запросы идут к JSON HSReplay напрямую (через локальный
  FlareSolverr или общий клиент), платные рендер-провайдеры не используются.
- **Ограничения:** рейтинги `TOP_5_PERCENT` и `TOP_1_PERCENT` HSReplay для этих
  запросов не поддерживает; позиции есть только в `TOP_50_PERCENT`; данные
  отстают от игры на время пересчёта HSReplay (см. `as_of`).
