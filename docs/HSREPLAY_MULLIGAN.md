# HSReplay: муллиган колод (mulligan v2)

Pipeline-источник `hsreplay_mulligan` собирает с HSReplay муллиган v2 для
топ-колод Стандарта и Вольного на четырёх рангах: по каждой карте процент,
сколько её оставляют, винрейт, если карта в стартовой руке, при доборе и при
розыгрыше, сколько ходов карта лежит в руке и на каком ходу её разыгрывают.
Срезы: кто ходит (все партии, первым ходом, с монеткой), период и, с
премиум-сессией, класс соперника. Данные хранятся снимками в SQLite
`hsreplay_mulligan_*` с зеркалом в PostgreSQL `analytics.hsreplay_mulligan_*`
и отдаются через REST `/v1/hsreplay/mulligan*` и GraphQL `hsreplayMulligan`.
На сайте пока не показываются.

## Что отдаёт HSReplay

Проверено 25.10.2026 живыми запросами через Scrape.do (`render=false`).

```text
GET https://hsreplay.net/api/v1/mulligan/
    ?shortid=<deck>                     # id колоды из /decks/<shortid>/
    &game_type_filter=RANKED_STANDARD   # или RANKED_WILD
    &league_rank_range=LEGEND           # DIAMOND_FOUR_THROUGH_DIAMOND_ONE, DIAMOND, BRONZE_THROUGH_GOLD
    &player_initiative=COIN             # ALL, FIRST, COIN
    &time_range=LAST_30_DAYS            # CURRENT_PATCH, CURRENT_EXPANSION
```

Ответ — объект с ключом `ALL` и, при премиуме, ключом каждого из 11 классов:

```json
{
  "metadata": {"selected_params": {"PlayerInitiative": "COIN", "LeagueRankRange": "LEGEND", "TimeRange": "LAST_30_DAYS"}},
  "ALL": {
    "query_score_scope": "deck",
    "num_games": 1904,
    "base_winrate": 44.64,
    "as_of": "2026-10-25T12:41:53Z",
    "metadata": {"deck_games": 1904, "deck_winrate": 44.64, "archetype_games": 347280, "archetype_winrate": 49.93, "class_games": 1752968, "class_winrate": 48.32},
    "cards_by_dbf_id": [
      {"dbf_id": 129793, "keep_percentage": 100.0, "opening_hand_winrate": 42.0, "winrate_when_drawn": 38.46,
       "winrate_when_played": 39.0, "avg_turns_in_hand": 1.66, "avg_turn_played_on": 4.29}
    ]
  },
  "MAGE": {"query_score_scope": "archetype", "num_games": 18646, "...": "..."}
}
```

- `query_score_scope` — откуда цифры. `deck`: игр этой колоды хватило.
  `archetype` или `class`: HSReplay подставил данные архетипа или класса,
  потому что игр колоды мало. Мы храним и отдаём это поле как `scope`.
- `num_games` и `base_winrate` относятся к той выборке, которую указывает
  `scope`.
- Счётчиков («сколько раз оставили») HSReplay не отдаёт, только проценты и
  средние.

Доступ (25.10.2026):

| Срез | Без сессии | С премиум-сессией |
| --- | --- | --- |
| Бронза–Золото, `ALL` | 200, только группа `ALL` | 200, `ALL` + 11 классов |
| Любой другой ранг или `FIRST`/`COIN` | HSReplay 403 (Scrape.do отвечает 502 `ROTATION_FAILED`, 0 кредитов) | 200, `ALL` + 11 классов |

Коды HSReplay:

| Ответ | Что значит | Что делаем |
| --- | --- | --- |
| 200 + JSON | Данные | Снимок, если цифры изменились |
| 202 с пустым телом или `{"msg": "Query is processing…"}` | Холодный запрос ещё считается. `LAST_30_DAYS` у колод посчитан заранее, а `CURRENT_EXPANSION` и `CURRENT_PATCH` HSReplay считает по первому запросу: 25.10.2026 `current_expansion` был готов минут через 4, `current_patch` отвечал 202 и через 6 минут | Срез получает состояние `processing` и снова становится в очередь через 1 час, потом через 2, 4 … до 24 часов, пока HSReplay не досчитает, чтобы один срез не съедал бюджет каждого запуска. Запуск из-за этого не считается неудачным |
| 204 | В срезе нет игр этой колоды | Срез проверен, `no_data`, снимок не пишется |
| 401/403 (у Scrape.do — 502 `ROTATION_FAILED`) | HSReplay не принял сессию | `premium_denied`, см. «Премиум-сессия» |
| 400/404 | Фильтр не поддерживается или колоды нет | `unsupported`, повтор через неделю |

## Какие колоды собираем

Набор колод — верх `analytics/query/list_decks_by_win_rate_v2` за 30 дней для
каждой пары «формат × ранг» (8 списков, раз в сутки, по 1 кредиту):

| Формат | Ранги | Колод на ранг |
| --- | --- | --- |
| Стандарт | Легенда, Алмаз 4–1, Алмаз, Бронза–Золото | 10 самых популярных + 2 с лучшим винрейтом (не меньше 1000 игр) |
| Вольный | те же | 5 самых популярных + 1 с лучшим винрейтом |

В выборку попадают колоды, у которых не меньше 200 игр. Колода, выпавшая из
списка, становится неактивной: её срезы больше не обновляются, но последние
данные по-прежнему читаются. Для каждой колоды в её ранге планируются все 9
срезов: `all`/`first`/`coin` × `last_30_days`/`current_patch`/`current_expansion`.
Одна и та же колода может стоять в нескольких рангах; это разные срезы.

Итого по умолчанию: 72 пары «колода × ранг», **648 срезов**.

## Очередь и бюджет

Каждый срез — цель с собственным состоянием. Цель «просрочена», если её ни
разу не читали или истёк TTL:

| Период | TTL, tier 1 | tier 2 (×2) | tier 3 (×3) |
| --- | ---: | ---: | ---: |
| `current_patch` | 48 ч | 96 ч | 144 ч |
| `last_30_days` | 72 ч | 144 ч | 216 ч |
| `current_expansion` | 168 ч | 336 ч | 504 ч |

Tier 1 — Стандарт, Легенда и Алмаз 4–1; tier 2 — Стандарт, Алмаз и
Бронза–Золото; tier 3 — Вольный. Запуск читает просроченные цели по tier,
сначала никогда не читанные, затем самые просроченные. Неизменившийся ответ
(цифры те же, поменялось только `as_of`) не пишет снимок, только отмечает
проверку.

Бюджет Scrape.do:

| Что | Сколько |
| --- | --- |
| Запросы муллигана по TTL | ≈129 в сутки |
| Списки колод | 8 в сутки |
| Проверка сессии `/api/v1/account/` | 1 за запуск, 8 в сутки |
| **Итого** | **≈145 кредитов в сутки** |
| Лимит на запуск | `HS_HSREPLAY_MULLIGAN_SCRAPE_DO_RUN_CREDITS=60` |
| Лимит на сутки UTC | `HS_HSREPLAY_MULLIGAN_SCRAPE_DO_DAILY_CREDITS=160` (считается по всем запускам суток) |
| Параллельно | `HS_HSREPLAY_MULLIGAN_CONCURRENCY=5`, больше 5 не бывает: у аккаунта Scrape.do 50 параллельных запросов, 40 занимает муллиган HSGuru |

Запрос уходит, только если потраченные кредиты, запросы в полёте и ещё один
укладываются в лимит, поэтому параллельные ответы не выходят за лимит.
Отказ HSReplay (502) Scrape.do не списывает. Холодный `202` списывает 1
кредит; в том же запуске такой срез повторно не запрашивается.

Первое наполнение 648 срезов занимает около четырёх суток лимита. Потом
очередь держится на TTL. План на текущих данных показывает
`python -m app.cli refresh-hsreplay-mulligan --estimate`.

Таймер `hs-data-api-docker-refresh-hsreplay-mulligan.timer`: каждые 3 часа
в :50, начиная с 01:50 (Europe/Warsaw), 8 запусков в сутки, тайм-аут 50
минут, дедлайн запуска 40 минут. Ручной запуск:

```bash
docker compose run --rm api python -m app.cli refresh-hsreplay-mulligan
```

Остальные настройки (`HS_HSREPLAY_MULLIGAN_*`): `STANDARD_POPULAR_DECKS` (10),
`STANDARD_WINRATE_DECKS` (2), `WILD_POPULAR_DECKS` (5), `WILD_WINRATE_DECKS`
(1), `DECK_MIN_GAMES` (200), `WINRATE_MIN_GAMES` (1000), `DEADLINE_SECONDS`
(2400), `SNAPSHOT_DAYS` (30).

## Премиум-сессия

Используется та же сессия, что у остальных источников HSReplay
(`app/hsreplay_auth.py`, файл `hsreplay-auth.json`): cookie уходит в
Scrape.do заголовком `Sd-Cookie` и нигде не логируется.

Каждый запуск сначала проверяет сессию запросом `GET /api/v1/account/`
(1 кредит):

| `premium_session.state` | Когда |
| --- | --- |
| `ok` | `is_premium: true` |
| `not_premium` | Аккаунт ответил, но премиума нет |
| `denied` | HSReplay отказал (401/403); или во время запуска 3 премиум-запроса подряд получили отказ при ни одном успешном |
| `missing_session` | Нет cookie сессии |
| `unknown` | Проверка не дала ответа (сеть, исчерпан лимит). Сбор идёт как обычно, отказы считаются по ходу запуска. Если прошлый запуск видел `denied`/`not_premium`/`missing_session`, этот статус сохраняется (`detail` с пометкой `kept`), пока проверка не ответит однозначно |

При `denied`, `not_premium` и `missing_session`:

- премиум-срезы и премиум-списки колод не запрашиваются (кредиты не
  тратятся), собираются только бесплатные срезы Бронзы–Золота `all`;
- уходит Telegram-алерт `premium_auth_failed` (с дедупликацией);
- источник получает состояние `partial`, а в `detail` статуса стоит
  `PREMIUM SESSION DENIED: …` (`/v1/sources`, панель);
- `GET /v1/hsreplay/mulligan` по премиум-срезу без собранных данных отвечает
  `503` с `detail.premium_session`, а не пустым списком. Если данные уже
  есть, ответ `200`, в `data.premium_session` виден статус, а `stale`
  включится, когда данные устареют;
- запрос `opponent_class`, которого нет в бесплатном ответе, отвечает `503`
  с объяснением;
- GraphQL отвечает ошибкой `PREMIUM_SESSION_UNAVAILABLE` с
  `extensions.premiumSession`.

Восстановление: `python -m app.cli hsreplay-login` (или импорт cookie), затем
следующий запуск сам вернёт сбор премиум-срезов.

## REST

Все ответы: `{"data": …, "meta": {"source_id": "hsreplay_mulligan", "fetched_at", "stale", "count"}}`.

### `GET /v1/hsreplay/mulligan`

| Параметр | Значения | По умолчанию |
| --- | --- | --- |
| `deck` | shortid колоды HSReplay (обязательно) | — |
| `format` | `standard`, `wild` | любой |
| `rank` | `legend`, `diamond_4to1`, `diamond`, `bronze_gold` | `legend` |
| `initiative` | `all`, `first` (первым ходом), `coin` (с монеткой) | `all` |
| `period` | `last_30_days`, `current_patch`, `current_expansion` | `last_30_days` |
| `opponent_class` | `any` или класс заглавными (`MAGE`, `DEATHKNIGHT`, …) | `any` |

Поля `data`:

| Поле | Что это |
| --- | --- |
| `source` | Всегда `"hsreplay"`: по нему клиент отличает этот ответ от `/v1/hsguru/mulligan` |
| `deck_id`, `format`, `rank`, `initiative`, `period`, `opponent_class` | Срез |
| `premium` | Нужен ли для среза премиум |
| `deck` | `archetype_id`, `archetype_name`, `player_class`, `deck_list` (как у HSReplay: `[[dbf_id, count], …]`) |
| `games`, `base_winrate` | Выборка и винрейт группы `opponent_class` |
| `scope` | `deck`, `archetype` или `class` (см. выше) |
| `sample` | `deck_games`/`deck_winrate`, `archetype_games`/`…`, `class_games`/`…` |
| `opponent_classes` | Какие группы есть в снимке (`any` + классы при премиуме) |
| `state`, `no_data` | Состояние среза; `no_data: true` — HSReplay ответил 204 |
| `snapshot_id`, `changed_at`, `checked_at`, `stale`, `active` | Снимок, когда цифры менялись, когда проверялись; `stale` — проверке больше двух TTL или колода выпала из набора |
| `premium_session` | Статус сессии из последнего запуска |
| `source_url` | Страница колоды на HSReplay с теми же фильтрами |
| `rows[]` | Карты в порядке HSReplay |

Поля `rows[]`: `dbf_id`, `card_id`, `card_name` (английское имя из
HearthstoneJSON), `keep_percentage`, `opening_hand_winrate`,
`winrate_when_drawn`, `winrate_when_played`, `avg_turns_in_hand`,
`avg_turn_played_on`, а также `opening_hand_impact`, `drawn_impact`,
`played_impact`: соответствующий винрейт минус `base_winrate`, в
процентных пунктах.

### Остальные эндпоинты

| Endpoint | Параметры | Что возвращает |
| --- | --- | --- |
| `GET /v1/hsreplay/mulligan/decks` | `format`, `rank`, `limit` | Набор колод: формат, ранг, позиция, `pick_reason` (`popular`/`winrate`), архетип, класс, игры, винрейт, `slices_planned`, `slices_with_data`, `checked_at` |
| `GET /v1/hsreplay/mulligan/slices` | `deck`, `format` | Все срезы колоды: `rank`, `initiative`, `period`, `premium`, `state`, `last_success_at`, `last_changed_at`, `scope`, `groups_total` |
| `GET /v1/hsreplay/mulligan/history` | срез + `card`, `limit` | Снимки среза (games, base_winrate, scope, as_of); с `card` (dbf id, card id или имя) — строка карты в каждом снимке |
| `GET /v1/hsreplay/mulligan/coverage` | `runs` | `premium_session`, покрытие по формату × рангу × монетке × периоду, последние запуски (`requests`, `scrape_do_credits`, `stop_reason`, `detail`), настройки. Без кэша |

## Сравнение с HSGuru

| | HSReplay (`/v1/hsreplay/mulligan`) | HSGuru (`/v1/hsguru/mulligan`) |
| --- | --- | --- |
| Объект | Колода (shortid) | Архетип или колода HSGuru |
| Ранги | `legend`, `diamond_4to1`, `diamond`, `bronze_gold` | `all`, `diamond_4to1`, `diamond_to_legend`, `legend`, `top_5k`, `top_legend` |
| Монетка | `initiative`: `all`/`first`/`coin` | `coin`: `any_player`/`going_first`/`on_coin` |
| Класс соперника | `opponent_class` (премиум, одна выборка на все классы) | `opponent_class` |
| Карта в стартовой руке | `opening_hand_winrate`, `opening_hand_impact` | `kept_impact` (оставлена), `mulligan_impact` (была в муллигане) |
| Добор | `winrate_when_drawn`, `drawn_impact` | `drawn_impact`, `not_drawn_impact` |
| Розыгрыш | `winrate_when_played`, `played_impact`, `avg_turn_played_on` | нет |
| Оставляют | `keep_percentage` | нет (есть `kept_count`) |
| Счётчики | нет | `*_count` |

Общие ключи для сравнения: `dbf_id` карты и `*_impact` в процентных пунктах
относительно винрейта выборки. Ближайшие пары — `opening_hand_impact` ↔
`kept_impact` и `drawn_impact` ↔ `drawn_impact`. Определения у источников
разные (у HSReplay «в стартовой руке после муллигана», у HSGuru «оставлена в
муллигане»), поэтому сравнивать стоит направление и порядок карт, а не
десятые доли.

## Хранение

| Таблица SQLite (и `analytics.*` в PostgreSQL) | Что |
| --- | --- |
| `hsreplay_mulligan_decks` | Набор колод по формату и рангу |
| `hsreplay_mulligan_targets` | Срезы и их состояние (`pending`, `ok`, `no_data`, `processing`, `premium_denied`, `unsupported`, `failed`), очередь |
| `hsreplay_mulligan_snapshots` | Снимок ответа (только если цифры изменились) |
| `hsreplay_mulligan_groups` | Группы снимка: `ALL` и классы, с `games`, `base_winrate`, `scope`, выборками |
| `hsreplay_mulligan_cards` | Строки карт каждой группы |
| `hsreplay_mulligan_runs` | Запуски: запросы, кредиты, статус сессии, причина остановки |

Снимки старше 30 дней удаляются, кроме последнего снимка каждого среза.
Миграция PostgreSQL — `platform/sql/019_hsreplay_mulligan.sql` (там же
представление `hub.hsreplay_mulligan_latest`); дельта
`platform/scripts/stage-analytics.py` переносит все шесть таблиц.

## Живой прогон 25.10.2026

Код из этой ветки запускался в production-контейнере (`docker compose run`)
с отдельным каталогом данных и production-сессией HSReplay (только чтение).

| Запуск (UTC) | Что сделано | Запросов / кредитов | Время |
| --- | --- | ---: | ---: |
| 16:48 | Сессия `ok` (`is_premium: true`). 8 списков колод → 72 колоды (Стандарт 4 × 12, Вольный 4 × 6), 648 срезов в плане. 72 среза `last_30_days` (24 колоды Легенды и Алмаза 4–1 × `all`/`first`/`coin`) с данными; у всех `ALL` + 11 классов, 13 640 строк карт, 821 группа из 823 со `scope: deck`. 79 срезов `current_patch` ответили 202 с пустым телом | 160 / 160 | 1 мин 29 с |
| 16:51 | Те же 79 срезов `current_patch` и 20 `current_expansion` — снова 202 | 100 / 100 | 1 мин 8 с |
| 16:56 | Окончательная версия: 29 срезов `current_patch` — `processing`, запуск `ok`, код выхода 0 | 30 / 30 | < 1 мин |

Первые два запуска шли на промежуточной версии кода: пустой 202 считался
204, а потом ошибкой. Это исправлено и закрыто тестами. Отдельные пробы:
`current_expansion` готов примерно через 4 минуты после первого запроса,
`current_patch` через 8 минут ещё отвечал 202. Всего за проверку ушло около
300 кредитов (три запуска и 10 проб). В обычной работе действует суточный
лимит 160.

Пример (сокращён, `rows` — первые три карты):

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsreplay/mulligan?deck=Tqcm39067GQSMafXBbErHe&rank=legend&initiative=coin"
```

```json
{
  "data": {
    "source": "hsreplay",
    "source_id": "hsreplay_mulligan",
    "deck_id": "Tqcm39067GQSMafXBbErHe",
    "format": "standard",
    "rank": "legend",
    "initiative": "coin",
    "period": "last_30_days",
    "opponent_class": "any",
    "premium": true,
    "deck": {"archetype_id": 19, "archetype_name": "Драконо-воин", "player_class": "WARRIOR", "deck_list": "[[115365,2],[123164,2],…]"},
    "state": "ok",
    "no_data": false,
    "games": 229360,
    "base_winrate": 49.59,
    "scope": "deck",
    "as_of": "2026-10-25T12:41:53Z",
    "sample": {"deck_games": 229360, "deck_winrate": 49.59, "archetype_games": 726603, "archetype_winrate": 49.18, "class_games": 1110602, "class_winrate": 47.01},
    "opponent_classes": ["any", "DEATHKNIGHT", "DEMONHUNTER", "DRUID", "HUNTER", "MAGE", "PALADIN", "PRIEST", "ROGUE", "SHAMAN", "WARLOCK", "WARRIOR"],
    "snapshot_id": 50,
    "changed_at": "2026-10-25T16:49:34.236719+00:00",
    "checked_at": "2026-10-25T16:49:34.236719+00:00",
    "active": true,
    "stale": false,
    "premium_session": {"state": "ok", "checked_at": "2026-10-25T16:56:41.518615+00:00", "detail": null},
    "source_url": "https://hsreplay.net/decks/Tqcm39067GQSMafXBbErHe/#gameType=RANKED_STANDARD&rankRange=LEGEND&timeRange=LAST_30_DAYS&playerInitiative=COIN&tab=mulligan-guide",
    "rows": [
      {"dbf_id": 117714, "card_id": "TLC_600", "card_name": "Windpeak Wyrm", "keep_percentage": 27.04, "opening_hand_winrate": 49.0, "winrate_when_drawn": 48.51, "winrate_when_played": 49.08, "avg_turns_in_hand": 2.38, "avg_turn_played_on": 6.61, "opening_hand_impact": -0.59, "drawn_impact": -1.08, "played_impact": -0.51},
      {"dbf_id": 123157, "card_id": "CATA_582", "card_name": "Searing Fissure", "keep_percentage": 10.45, "opening_hand_winrate": 49.17, "winrate_when_drawn": 47.97, "winrate_when_played": 46.52, "avg_turns_in_hand": 1.84, "avg_turn_played_on": 6.02, "opening_hand_impact": -0.42, "drawn_impact": -1.62, "played_impact": -3.07},
      {"dbf_id": 123163, "card_id": "CATA_584", "card_name": "Erupting Volcano", "keep_percentage": 13.33, "opening_hand_winrate": 48.57, "winrate_when_drawn": 46.59, "winrate_when_played": 44.34, "avg_turns_in_hand": 1.53, "avg_turn_played_on": 6.1, "opening_hand_impact": -1.02, "drawn_impact": -3.0, "played_impact": -5.25}
    ]
  },
  "meta": {"source_id": "hsreplay_mulligan", "fetched_at": "2026-10-25T16:49:34.236719+00:00", "stale": false, "count": 16}
}
```

Та же колода против Мага (`opponent_class=MAGE`): `games` 17 184,
`base_winrate` 59.5, `scope: deck`; у Windpeak Wyrm `opening_hand_impact`
+1.4. Срез, который HSReplay ещё считает:

```json
{"detail": "HSReplay is still computing this slice (HTTP 202); the next check is after 2026-10-25T17:57:20+00:00"}
```

Набор колод (`/v1/hsreplay/mulligan/decks?format=standard&rank=legend`),
первая строка:

```json
{"format": "standard", "rank": "legend", "deck_id": "Tqcm39067GQSMafXBbErHe", "archetype_id": 19, "archetype_name": "Драконо-воин", "player_class": "WARRIOR", "total_games": 30392, "win_rate": 54.73, "pick_reason": "popular", "position": 0, "slices_planned": 9, "slices_with_data": 3}
```

`total_games` в наборе — игры колоды в списке HSReplay за 30 дней на этом
ранге. `games` муллигана считается по своей выборке (`scope`), поэтому числа
не обязаны совпадать.
