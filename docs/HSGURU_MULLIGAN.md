# HSGuru: муллиган и матчапы по классам

Pipeline-источник `hsguru_mulligan` собирает с HSGuru муллиган и матчапы по
классам для всех архетипов и колод каталога, у которых достаточно игр, по всем
срезам HSGuru: формат, ранг, период (включая окно банов `*_ban`), монетка и —
для самых популярных архетипов — класс соперника. Данные хранятся в SQLite
`hsguru_*` с зеркалом в PostgreSQL `analytics.hsguru_*` и отдаются через REST
`/v1/hsguru/mulligan*`, `/v1/hsguru/class-matchups` и GraphQL
`hsguruMulligan` / `hsguruClassMatchups`.

## Что отдаёт HSGuru

Проверено 25.10.2026 на живых страницах.

| Страница | Что на ней | Фильтры |
| --- | --- | --- |
| `/card-stats?archetype=…` и `/card-stats?deck_id=…` | Муллиган по каждой карте: Mulligan Impact/Count, Drawn Impact/Count, Not Drawn Impact/Count, Kept Impact/Count; размер выборки `Games: N` | `format` (2 Standard, 1 Wild), `rank` (`all`, `bronze_plat`, `diamond_4to1`, `diamond_to_legend`, `legend`, `top_5k`, `top_legend`), `period` (`past_3_hours` … `past_30_days`, `patch_*`, окно дополнения `black_empire`, окно банов `black_empire_ban`), `player_has_coin` (`yes` — с монеткой, `no` — первым ходом), `opponent_class` (11 классов), `min_mull_count`, `min_drawn_count` |
| `/archetype/<name>` | Винрейт архетипа против каждого класса (игры и доля) и строка Total | `format`, `rank`, `period`, `player_has_coin` |
| `/deck/<id>` | То же для конкретной колоды, плюс код колоды | `rank`, `period`, `player_has_coin` |
| `/meta` | Архетипы: winrate, popularity, games, средние ходы, длительность, climbing speed | `format`, `rank`, `period`, `player_has_coin` |
| `/matchups` | Матрица архетип × архетип | `format`, `rank`, `period`, пороги выборки |
| `/decks` | Колоды архетипа: winrate, игры, ходы, длительность | `format`, `rank`, `period`, `min_games`, `order_by`, монетка, класс соперника, регион |

Пустой срез HSGuru отдаёт таблицу без строк и `Games: 0` (матчапы — строку
Total с нулями). Поэтому «нет данных» надёжно отличается от сломанной страницы
или Cloudflare-заглушки. Премиум-фильтры показывают «You do not have access to
these filters»: такая страница отклоняется и уходит в залогиненный платный
профиль.

На HSGuru **нет** статистики «сыграна»: винрейта при розыгрыше, среднего хода
розыгрыша, походовой статистики карт — это данные HSReplay. Походовые данные
HSGuru — это только средние `turns` и `duration` на уровне архетипа и колоды
(`/meta`, `/decks`). Они уже есть в `hsguru_meta_matrix` и в каталогах колод.

## Что собирали раньше и что собираем теперь

| Данные | До | Теперь (`hsguru_mulligan`) |
| --- | --- | --- |
| Муллиган архетипа | `hsguru_archetype_analysis`: только Legend + `past_week`, без монетки; хранились лишь карты с ≥25 муллиганами и ≥25 добором; без Not Drawn; в имени карты — служебный префикс `# ↑x (2)`; раз в двое суток через Scrape.do | 6 рангов × 8 периодов (в том числе `black_empire_ban`) для архетипов с ≥2000 игр; монетка (с монеткой / первым ходом) для `past_week` и накопительных окон при ≥3000 играх на сторону; по классу соперника — текущее окно, ранги All и Legend, ≥10000 игр. Все колонки, включая Not Drawn, полная таблица (порог задаёт клиент), чистые имена карт |
| Муллиган колоды | не было | Каждая колода каталогов HSGuru с ≥100 играми в своём срезе; монетка — при ≥600 играх |
| Матчапы по классам | Legend + `past_week` для архетипов | Архетипы: все ранги, `past_week` и накопительные окна; колоды: свой срез |
| История | только последний набор | Снимки на каждое изменение страницы, 30 дней (последний снимок хранится всегда) |
| Матрица архетип × архетип | 3 среза (`hsguru_matchups_*`) | без изменений (см. «Что дальше») |
| Мета и каталоги колод | `hsguru_meta_matrix`, `hsguru_deck_catalog_*` | без изменений — это входные данные планировщика |

На данных 25.10.2026 план содержит **8 289 страниц**:

| Группа | Страниц | Страниц/сутки по TTL |
| --- | ---: | ---: |
| Муллиган архетипа, любая монетка (tier 1) | 3 031 | 607 |
| Муллиган архетипа по монетке (tier 2) | 1 369 | 262 |
| Муллиган архетипа против класса (tier 3) | 682 | 49 |
| Матчапы архетипа по классам (tier 3) | 1 499 | 143 |
| Муллиган колоды (tier 2) | 762 | 90 |
| Муллиган колоды по монетке (tier 3) | 184 | 12 |
| Матчапы колоды по классам (tier 3) | 762 | 45 |
| **Итого** | **8 289** | **≈1 210** |

Пороги — компромисс с пропускной способностью FlareSolverr (см. ниже). При
порогах 1000 / 1500 / 5000 и вдвое более коротких TTL план был бы 11 136 страниц
и ≈2 690 страниц в сутки; их можно включить переменными окружения, если
реальная скорость в проде окажется выше.

Посмотреть план на текущих данных, ничего не скачивая:

```bash
docker compose run --rm api python -m app.cli refresh-hsguru-mulligan --estimate
```

## Как работает обновление

Каждая страница — это *target* с собственным состоянием (`hsguru_stat_targets`).

1. **План.** Список целей строится из уже собранных данных: игры архетипа по
   срезам берутся из `hsguru_meta_matrix`, игры колод — из
   `hsguru_deck_catalog_{standard,wild}_{legend,all,fallback}`. Срез без
   достаточного числа игр не запрашивается. Ранги `diamond`, `top_500`,
   `top_100` в card-stats отсутствуют, а у `bronze_plat` нет игр в матрице,
   поэтому они не планируются. Окна 3 и 6 часов пропускаются: выборка на карту
   там слишком мала и постоянно меняется.
2. **Когда страница должна обновиться.** Если её ещё не читали; если истёк
   TTL периода (`past_day` 24 ч, `past_3_days` и `past_week` 72 ч,
   `past_2_weeks` 7 суток, `past_30_days` 14 суток, накопительные окна 7 суток;
   у tier 3 TTL вдвое длиннее); если накопительное окно (патч, дополнение,
   баны) прибавило ≥30 % игр, но не чаще раза в сутки.
3. **Порядок.** Сначала tier 1 → 2 → 3. Внутри tier ещё не прочитанные
   страницы идут раньше, затем — самые просроченные. Дальше по периоду:
   окно дополнения, окно банов, `past_week`, `past_day`, старые `patch_*`,
   `past_3_days`, `past_2_weeks`, `past_30_days`. При равенстве — больше игр.
4. **Бюджет запуска.** До 200 страниц и 60 минут, concurrency 2. Остальное
   остаётся в очереди на следующий запуск. Перечтения из-за роста игр идут
   сверх оценки по TTL, но общий объём ограничен бюджетом запусков; при
   нехватке ждут цели нижних tier.
5. **Неизменившаяся страница** не создаёт снимок — обновляются только
   `last_success_at` и число игр. Ошибка откладывает цель с экспоненциальной
   паузой (30 мин × 2ⁿ, максимум 24 ч). После 8 ошибок подряд запуск
   останавливается (`provider_circuit_open`).
6. **Цели, выпавшие из плана** (например, архетип потерял игры), остаются
   читаемыми с `active=false` и `stale=true`.

Таймер `hs-data-api-docker-refresh-hsguru-mulligan.timer` запускается каждые
3 часа в :25 по Europe/Warsaw:

```bash
python -m app.cli refresh-hsguru-mulligan --scheduled --schedule-id refresh-hsguru-mulligan
```

## Расход Scrape.do и бюджет

Фактический расход до этой задачи (`data/provider-usage.sqlite3`, 21–25.10.2026):
HSGuru забирает **20 615 из ~22 500 кредитов Scrape.do** за 4,5 суток, то есть
около 2–9 тыс. кредитов в сутки. Из них 8 865 кредитов (1 775 вызовов) пришлись на
`hsguru_archetype_analysis`. Страница HSGuru стоит 5 кредитов (datacenter
render), 10 (residential) или 25 (residential render); дешёвый профиль за
1 кредит Cloudflare не пропускает.

Если читать ≈1 210 страниц в сутки через Scrape.do, это было бы ≈6 тыс.
кредитов в сутки (≈180 тыс. в месяц). Поэтому новый pipeline устроен так:

- **Основной маршрут — локальный FlareSolverr, 0 кредитов.** По политике
  прокси HSGuru у FlareSolverr идёт напрямую, residential-прокси не тратится.
  Замер 25.10.2026: 10 из 10 разных срезов (премиум-ранги, баны, монетка,
  класс соперника) прошли последовательно за 11–13 с на страницу без
  премиум-заглушки. Прогон pipeline на 12 самых тяжёлых страницах (ранг All,
  окно патча) при concurrency 2 и загруженном сервере: 11 из 12 успешно,
  ≈20 с на страницу, то есть ≈175 страниц в час; одна страница получила
  HTTP-ошибку FlareSolverr и ушла в повтор через 30 минут.
- **Scrape.do — только резерв** (без Decodo, Firecrawl, Bright Data и
  Scrapfly), когда FlareSolverr не справился или страница требует
  премиум-сессию. Действуют жёсткие лимиты: не больше
  `HS_HSGURU_MULLIGAN_SCRAPE_DO_RUN_CREDITS` (по умолчанию 300) за запуск и
  `HS_HSGURU_MULLIGAN_SCRAPE_DO_DAILY_CREDITS` (1 000) за UTC-сутки. Перед
  каждым платным вызовом резервируется 25 кредитов (худший профиль), поэтому
  лимит не превышается. Итого не больше ~30 тыс. кредитов в месяц.
- **Общий месячный бюджет HSGuru.** Платные вызовы идут как
  `job_class="detail"` Meta-Lab: при включённом учёте
  (`HS_META_LAB_ENABLED`) они прекращаются на пороге `throttle` (по умолчанию
  620 тыс. из `hard` 700 тыс. кредитов в месяц), раньше критичных meta-задач.
- После 4 сбоев FlareSolverr подряд он в этом запуске больше не используется.
  Когда кончается и лимит Scrape.do, запуск останавливается
  (`scrape_do_budget_exhausted`), а очередь ждёт следующего таймера.

Пропускная способность: 8 запусков × 200 страниц = 1 600 страниц в сутки
(≈8 часов работы FlareSolverr) против ≈1 210 по TTL. Первичная загрузка всех
8 289 страниц займёт около 5–6 суток. Tier 1 (3 031 страница) загрузится
первым, примерно за 2 суток.

Каждый запуск пишет строку в `hsguru_stat_runs`: страницы бесплатного и
платного маршрута, потраченные кредиты, лимит, причину остановки. Сводка — в
`GET /v1/hsguru/mulligan/coverage`.

## Настройки

Все переменные необязательны; это целые числа.

| Переменная | По умолчанию | Смысл |
| --- | ---: | --- |
| `HS_HSGURU_MULLIGAN_ARCHETYPE_MIN_GAMES` | 2000 | Минимум игр архетипа в срезе |
| `HS_HSGURU_MULLIGAN_COIN_MIN_GAMES` | 3000 | Минимум игр на сторону монетки |
| `HS_HSGURU_MULLIGAN_OPPONENT_MIN_GAMES` | 10000 | Минимум игр для разбивки по классу соперника |
| `HS_HSGURU_MULLIGAN_DECK_MIN_GAMES` | 100 | Минимум игр колоды |
| `HS_HSGURU_MULLIGAN_DECK_COIN_MIN_GAMES` | 600 | Минимум игр колоды для разбивки по монетке |
| `HS_HSGURU_MULLIGAN_MAX_PAGES` | 200 | Страниц за запуск |
| `HS_HSGURU_MULLIGAN_DEADLINE_SECONDS` | 3600 | Время запуска |
| `HS_HSGURU_MULLIGAN_CONCURRENCY` | 2 | Параллельных страниц (1–4) |
| `HS_HSGURU_MULLIGAN_SCRAPE_DO_RUN_CREDITS` | 300 | Лимит кредитов Scrape.do за запуск |
| `HS_HSGURU_MULLIGAN_SCRAPE_DO_DAILY_CREDITS` | 1000 | Лимит кредитов Scrape.do за UTC-сутки |
| `HS_HSGURU_MULLIGAN_SNAPSHOT_DAYS` | 30 | Хранение старых снимков |

## REST API

Срез задаётся одинаково во всех эндпоинтах:

| Параметр | Значения | По умолчанию |
| --- | --- | --- |
| `archetype` или `deck_id` | ровно один из них | — |
| `format` | `standard`, `wild`; обязателен с `archetype` | — |
| `rank` | `all`, `diamond_4to1`, `diamond_to_legend`, `legend`, `top_5k`, `top_legend` | `all` |
| `period` | период HSGuru или `current` — текущее окно меты (сейчас `black_empire`) | `current` |
| `coin` | `any_player`, `on_coin` (с монеткой), `going_first` (первым ходом) | `any_player` |
| `opponent_class` | `any` или `DEATHKNIGHT` … `WARRIOR` (только муллиган) | `any` |

Колоды каталога хранятся в своём срезе: обычно `rank=all` или `legend` и
текущий период. Узнать, какие срезы есть у архетипа или колоды, можно через
`/v1/hsguru/mulligan/slices`. Если срез не собран, ответ — `404` со ссылкой на
`/slices`. Неверная комбинация параметров — `422`.

### `GET /v1/hsguru/mulligan`

Дополнительные параметры: `min_mull_count`, `min_drawn_count` — локальный порог
выборки (HSGuru по умолчанию показывает 25/25; хранится полная таблица).

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsguru/mulligan?archetype=Dragon%20Warrior&format=standard&rank=legend&period=black_empire_ban&coin=on_coin&min_mull_count=25&min_drawn_count=25" | jq .
```

```json
{
  "data": {
    "kind": "cards",
    "subject_type": "archetype",
    "format": "standard",
    "archetype": "Dragon Warrior",
    "deck_id": null,
    "rank": "legend",
    "period": "black_empire_ban",
    "coin": "on_coin",
    "opponent_class": "any",
    "games": 9352,
    "winrate": null,
    "snapshot_id": 1842,
    "changed_at": "2026-10-25T10:25:41+00:00",
    "checked_at": "2026-10-25T10:25:41+00:00",
    "active": true,
    "stale": false,
    "source_url": "https://www.hsguru.com/card-stats?archetype=Dragon+Warrior&format=2&rank=legend&period=black_empire_ban&player_has_coin=yes&min_mull_count=0&min_drawn_count=0&show_counts=yes",
    "rows": [
      {
        "position": 0,
        "card_id": "BE_561",
        "dbf_id": 129947,
        "card_name": "Forge of Origination",
        "mulligan_impact": 3.5,
        "mulligan_count": 633,
        "drawn_impact": -0.6,
        "drawn_count": 1946,
        "not_drawn_impact": 0.6,
        "not_drawn_count": 1242,
        "kept_impact": 6.5,
        "kept_count": 314
      }
    ]
  },
  "meta": {"source_id": "hsguru_mulligan", "fetched_at": "2026-10-25T10:25:41+00:00", "stale": false, "count": 1}
}
```

Impact — изменение винрейта в процентных пунктах, как на HSGuru: Mulligan —
карта была в стартовой руке, Kept — оставлена, Drawn — взята до конца партии,
Not Drawn — не пришла. Count — число таких партий. `games` — выборка страницы,
`changed_at` — когда страница последний раз изменилась, `checked_at` — когда
её последний раз проверили. `stale=true`, если проверка старше двух TTL или
цель выпала из плана.

### `GET /v1/hsguru/class-matchups`

Те же параметры, кроме `opponent_class` и порогов. В `rows[]` —
`opponent_class` (`deathknight` … `warrior`), `winrate`, `games` и `share_pct`.
В `data.games` и `data.winrate` — строка Total.

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsguru/class-matchups?deck_id=41919872&period=current" | jq .
```

### `GET /v1/hsguru/mulligan/slices`

`archetype` или `deck_id` и необязательный `format`. Возвращает все сохранённые
срезы: `kind` (`cards` | `matchups`), ранг, период, монетку, класс соперника,
`state`, `last_success_at`, `last_games`, `active`.

### `GET /v1/hsguru/mulligan/history`

Параметры среза, а также `kind` (`cards` по умолчанию или `matchups`), `card`
(dbf id, card id или имя) и `limit` (1–500, по умолчанию 60). Возвращает
снимки среза от новых к старым (`snapshot_id`, `fetched_at`, `games`,
`winrate`, `rows_total`, `backend`). С `card` в каждой точке есть строка этой
карты.

### `GET /v1/hsguru/mulligan/coverage`

Служебная сводка: число целей по `subject_type` × `kind` × `tier` × `state`,
сколько из них с данными, самые старые и свежие проверки, последние запуски
(`runs`, 1–100) с расходом Scrape.do и действующие настройки. Ответ не
кэшируется.

## GraphQL

`hsguruMulligan(archetype, deckId, format, rank, period, coin, opponentClass,
minMullCount, minDrawnCount)` и `hsguruClassMatchups(archetype, deckId,
format, rank, period, coin)` возвращают JSON той же формы, что `data` в REST.
Читают зеркало в PostgreSQL, поэтому новые данные появляются там после
очередной синхронизации платформы.

```graphql
query {
  hsguruMulligan(archetype: "Dragon Warrior", format: "standard", rank: "legend", period: "past_week")
}
```

## Хранение

SQLite (`data/hs_parses.db`), зеркало — PostgreSQL `analytics.*`
(миграция `platform/sql/017_hsguru_mulligan.sql`):

| Таблица | Что в ней |
| --- | --- |
| `hsguru_stat_targets` | Одна строка на страницу: субъект, срез, tier, ожидаемые игры, состояние, время чтения, ошибки, `latest_snapshot_id` |
| `hsguru_stat_snapshots` | Снимок страницы при изменении: игры, винрейт, хэш содержимого, URL, бэкенд, кредиты |
| `hsguru_mulligan_cards` | Строки муллигана снимка |
| `hsguru_class_matchups` | Строки матчапов по классам |
| `hsguru_stat_runs` | Запуски: план, очередь, результаты, страницы и кредиты по маршрутам, причина остановки |

Представления `hub.hsguru_mulligan_latest` и `hub.hsguru_class_matchups_latest`
содержат последний снимок каждого среза.

## Что дальше

- После проверки нового pipeline в проде можно отключить
  `hsguru_archetype_analysis` (Legend/past_week через Scrape.do): его данные —
  подмножество новых. Это ≈2 тыс. кредитов в сутки (≈60 тыс. в месяц).
  Эндпоинт `/v1/hsguru/archetypes/analysis` тогда нужно перевести на новые
  таблицы. Там же стоит убрать служебный префикс `# ↑x (N)` из имён карт.
- Матрица архетип × архетип (`/matchups`) сейчас собирается для трёх срезов;
  её можно добавить в эту же очередь по рангам и периодам.
- Ранг `bronze_plat` есть в card-stats, но не в meta matrix, поэтому для него
  нет игр, по которым планировать цели.
