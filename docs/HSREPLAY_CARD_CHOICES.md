# HSReplay: что берут из Раскопки (card choices)

Источник `hsreplay_card_choices` отвечает на вопрос «что игроки берут из
Раскопки этой карты и что из этого выигрывает». Статистика выбора — с HSReplay,
полный пул вариантов — из базы карт HearthstoneJSON.

Термины — как в русском клиенте Hearthstone: Discover — «Раскопка» («вы
раскапываете»), Adapt — «Адаптация», Dredge — «Улов», Choose One — «Выберите
эффект», Excavate — «Добыча» («вы добываете сокровище»).

Код: `app/card_choice_pools.py` (карты с выбором и их пулы),
`app/hsreplay_card_choices.py` (сбор и чтение), `app/hsreplay_card_choices_store.py`
(SQLite), `app/routers/hsreplay_card_choices.py` (REST), GraphQL
`hsreplayCardChoices`.

## Эндпоинт HSReplay

```text
GET https://hsreplay.net/analytics/query/single_card_choices_by_winrate_v2/
    ?card_id=<dbf>&GameType=RANKED_STANDARD|RANKED_WILD&LeagueRankRange=LEGEND|BRONZE_THROUGH_GOLD|...
```

Ответ: `series.data.ALL[]` → `dbf_id`, `popularity`, `win_rate`, плюс `as_of`.

- `popularity` — как часто вариант берут, **когда он был среди предложенных**.
  Поэтому сумма по вариантам больше 100%.
- `win_rate` — винрейт партий, в которых этот вариант взяли.
- Числа игр нет, разбивки по классам нет.
- Отдаются только варианты с достаточной выборкой (до ~100 строк), а не весь
  пул. Одна строка приходит без `dbf_id` (HSReplay не может назвать вариант) —
  она не сохраняется, её количество лежит в `stats.unidentified_rows`.
- Проверено вживую 2026-10-25 (Scrape.do без рендера, 1 кредит): «Иллидариведение»
  (`CORE_YOP_001`, dbf 97377) — 9 вариантов в Стандарте на Легенде и
  Бронзе–Золоте, 31 в Вольном; «Птенец терродактиля» (Адаптация) и «Гниющий
  некромант» (Улов) в Вольном — 200; «Живые корни» («Выберите эффект») — 204;
  Добыча — 204 (это случайная награда, а не выбор).

## Карты с выбором

`choice_kind` определяет вид выбора по механикам и тексту коллекционной карты:

| `kind` | Механика | Статистика HSReplay | Пул |
| --- | --- | --- | --- |
| `discover` | Раскопка (Discover) | да | по тексту карты (ниже) |
| `adapt` | Адаптация | да | десять адаптаций Ун'Горо |
| `dredge` | Улов (Dredge) | да | нет: нижние карты своей колоды |
| `choose_one` | «Выберите эффект» (Choose One) | нет (204) | два варианта карты (`<id>a`/`<id>b`, у переизданий Core — у оригинала) |
| `excavate` | Добыча (Excavate) | нет (204) | сокровища по уровням: 1 — обычные, 2 — редкие, 3 — эпические, 4 — легендарное сокровище своего класса |

Стандарт определяется по собственной таблице карт HSReplay Стандарта
(`hsreplay_cards_legend_14d` и соседние): набор, в котором сыграно не меньше
10 карт, считается ротационным; карты набора `EVENT` — поштучно. Без этих
таблиц — запасной список наборов (Стандарт октября 2026).

## Полный пул Раскопки

Текст «Discover …» разбирается на фильтры: тип (карта/заклинание/существо/
оружие/локация), тип существа (Зверь, Демон, Дракон…), школа заклинаний,
ключевое слово (Изгой, Предсмертный хрип, Провокация, Секрет…), «Легендарная»,
стоимость (`N-Cost`, `costs (N) or more/less`), классовое правило (свой класс
и нейтральные; `from another class`; `from any class`; явно названные классы),
«из прошлого» (`from the past` — карты вне Стандарта) и явно перечисленные
карты (Дары героев). Варианты одного имени (переиздания Core) схлопываются.

Если пул зависит от партии («из вашей колоды», «в руке противника», «погибший
в этой игре», руны, «ещё не разыгранный тип»…) или текст не разобран, пул не
угадывается: `pool.resolved = false`, `pool.reason` = `game_state` или
`unparsed`. На 2026-10-25 в Стандарте 62 из 83 Раскопок имеют точный пул, 17
зависят от партии, 4 не разобраны; пул «Иллидариведения» совпадает с вариантами
HSReplay один в один.

У нейтральной карты пул зависит от класса игрока (`pool.class_dependent`):
отдаются карты всех классов, сайт может отфильтровать по классу колоды.

## Сбор

- Цели: 50 самых играемых карт с выбором Стандарта и 10 Вольного (по
  `times_played` таблиц карт HSReplay Легенды) × ранги `legend` и
  `bronze_gold`. Только `STATS_KINDS` (Раскопка, Адаптация, Улов). Ни разу не
  сыгранные карты не запрашиваются.
- Цель перечитывается, когда её не читали, когда прошла неделя (TTL 168 ч) или
  когда после последнего чтения вышел патч — через сутки после патча, чтобы
  новые числа набрали игр (`hearthstone_patches.published_at`).
- Бюджет: ~120 запросов в неделю по TTL. Scrape.do без рендера (1 кредит),
  не больше 5 параллельно, не больше 40 кредитов за запуск и 60 за сутки UTC,
  плюс 1 кредит на проверку сессии `/api/v1/account/`.
- HTTP 204 — пустой срез (`no_data`), 202 — HSReplay ещё считает (отступ 1, 2,
  4 … 24 ч), 400/404 — повтор через неделю, отказ сессии — запуск
  останавливается, алерт в Telegram, источник `partial`.
- Снимок пишется только при изменении чисел; иначе двигается
  `last_success_at`. Снимки старше 120 дней, на которые не указывает цель,
  удаляются.

Запуск: таймер `hs-data-api-docker-refresh-hsreplay-card-choices.timer`
ежедневно в 04:35 Europe/Warsaw (`python -m app.cli refresh-hsreplay-card-choices
--scheduled`). Оценка плана без запросов: `python -m app.cli
refresh-hsreplay-card-choices --estimate`. Разовый ограниченный прогон:
`--max-requests N`.

Настройки (переменные окружения): `HS_CARD_CHOICES_STANDARD_CARDS` (50),
`HS_CARD_CHOICES_WILD_CARDS` (10), `HS_CARD_CHOICES_RANKS` (`legend,bronze_gold`;
можно добавить `diamond_4to1`, `diamond`), `HS_CARD_CHOICES_CONCURRENCY` (5,
не больше 5), `HS_CARD_CHOICES_SCRAPE_DO_RUN_CREDITS` (40),
`HS_CARD_CHOICES_SCRAPE_DO_DAILY_CREDITS` (60), `HS_CARD_CHOICES_DEADLINE_SECONDS`
(1200), `HS_CARD_CHOICES_SNAPSHOT_DAYS` (120).

## Хранение

SQLite (`hs_parses.db`): `hsreplay_card_choice_targets` (состояние цели),
`hsreplay_card_choice_snapshots`, `hsreplay_card_choice_rows` (варианты снимка),
`hsreplay_card_choice_runs` (запуски, расход Scrape.do). Зеркало в PostgreSQL —
`analytics.hsreplay_card_choice_*` (миграция `platform/sql/020_hsreplay_card_choices.sql`,
представление `hub.hsreplay_card_choices_latest`), из него читает GraphQL.

## Ответ API

`GET /v1/hsreplay/card-choices?card=CORE_YOP_001&rank=legend`:

- `card` — `card_id`, `dbf_id`, `name`, `name_ru`, `kind`, `card_class`, `set`,
  `formats`, `text`.
- `stats` — `state` (`ok`, `no_data`, `not_collected` — карта вне собираемого
  топа, `not_tracked` — HSReplay не считает этот вид выбора, или состояние цели
  `pending`/`processing`/`failed`), `tracked`, `checked_at`, `changed_at`,
  `as_of`, `stale` (старше двух TTL), `source_url`, `options_total`,
  `unidentified_rows`.
- `options[]` — варианты HSReplay по убыванию `popularity`: `dbf_id`, `card_id`,
  `name`, `name_ru`, `card_class`, `cost`, `type`, `rarity`, `popularity`,
  `win_rate`, `in_pool`, `low_sample` (ровно 0 или 100 — горстка игр),
  `undervalued` (винрейт не меньше чем на 1 п. п. выше среднего по вариантам,
  а берут реже медианы; нужны хотя бы 3 надёжных варианта).
- `pool` — `kind`, `resolved`, `reason`, `class_dependent`, `total`, `cards[]`
  (те же поля карты + `has_stats`, `popularity`, `win_rate`; `has_stats: false`
  — «мало данных»), `tiers[]` (Добыча: `tier`, `cards[]`).
- `available[]` — какие формат/ранг собраны для карты.
- `premium_session` — статус сессии HSReplay последнего запуска.
