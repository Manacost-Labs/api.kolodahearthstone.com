# Официальные таблицы лидеров Blizzard

Pipeline-источник `blizzard_leaderboards` собирает официальные таблицы лидеров
Hearthstone с сайта Blizzard: все режимы, три региона, текущий сезон целиком и
все прошлые сезоны, которые ещё отдаёт API. Снимки хранятся по дням в
отдельной SQLite `blizzard_leaderboards.db` и отдаются через REST
`/v1/leaderboards*` и GraphQL `leaderboard*`.

## Что отдаёт Blizzard

Проверено 25.10.2026 (перепись первой страницы всех 490 таблиц).

```
GET https://hearthstone.blizzard.com/en-us/api/community/leaderboardsData
    ?region=EU&leaderboardId=standard&seasonId=156&page=1
```

* Ключ, cookies и прокси не нужны; Scrape.do не используется.
* Страница — 25 строк `{rank, accountid, rating?}` и `pagination`
  (`totalPages`, `totalSize`). Без `seasonId` отдаётся последний сезон.
  Страница за пределами таблицы — пустые `rows` и нули в `pagination`.
* `accountid` — BattleTag **без номера**. Ник не уникален: в одной таблице
  бывают два разных «EL7TE».
* Каждый ответ несёт `seasonMetaData[region][mode].seasons[]` — какие сезоны
  ещё доступны (новые первыми) — и `displayMetaData[mode]` (колонки,
  `seasonsWithStartDate` для ежемесячных сезонов).
* Без сжатия страница весит ~300 КБ из-за метаданных, с gzip — ~17 КБ.
  Сборщик всегда просит gzip.

| `leaderboardId` | Режим | Рейтинг | Сезоны (EU, 25.10.2026) | Текущий сезон, игроков EU / US / AP |
| --- | --- | --- | --- | --- |
| `standard` | Стандарт | — | 108–156 (49) | 16 625 / 9 249 / 9 308 |
| `wild` | Вольный | — | 108–156 (49) | 5 720 / 3 791 / 4 521 |
| `battlegrounds` | Поля сражений | MMR | 7–19 (13; AP ещё сезон 0) | 6 543 / 2 843 / 2 745 |
| `battlegroundsduo` | Поля сражений: дуо | MMR | 12–19 (8) | 2 028 / 959 / 531 |
| `arena` | Арена («Legacy Arena» у Blizzard) | среднее число побед | 36–56 (21) | 17 768 / 9 760 / 11 360 |
| `undergroundarena` | Подземная арена | очки | 1–10 (10) | 952 / 556 / 714 |
| `twist` | Twist | — | 116–136 (13) | 177 / 92 / 59 |

Есть ещё `classic` и `mercenaries`; они не собираются (включаются через
`HS_LEADERBOARDS_MODES`).

Всего API отдаёт 490 таблиц, ~476 000 страниц. Старые сезоны Полей сражений
огромны (сезон 7 EU — 1,5 млн игроков, 59 808 страниц), поэтому из каждой
таблицы хранятся первые `HS_LEADERBOARDS_MAX_PAGES` = 2000 страниц
(50 000 игроков); такая таблица помечена `truncated`. С этим ограничением
история — ~262 000 страниц (около недели фоновой дочитки: 20 запусков в день по 2000 страниц), текущие сезоны — ~4 300 страниц за обход, ~17 000 запросов в сутки.

## Как собирается

Обе команды берут один lock, поэтому Blizzard никогда не видит два запуска
одновременно.

| Таймер | Когда | Что делает |
| --- | --- | --- |
| `hs-data-api-docker-refresh-blizzard-leaderboards.timer` | каждые 6 часов в :10 (Europe/Warsaw) | `refresh-blizzard-leaderboards --scope live`: последний сезон каждой пары регион × режим целиком (~36 минут) |
| `hs-data-api-docker-refresh-blizzard-leaderboards-history.timer` | каждый час в :40, кроме 00, 06, 12 и 18 часов (там идёт живой обход) | `--scope history`: прошлые сезоны, до 2000 страниц и 25 минут за запуск; сначала сезоны, которые только что перестали быть текущими (их финальное чтение), затем от новых к старым. Когда история дочитана, запуск делает один запрос метаданных |

Вежливость: глобальный темп 2 запроса в секунду (`HS_LEADERBOARDS_RPS`), не
больше 2 одновременно (`HS_LEADERBOARDS_CONCURRENCY`), 4 попытки с паузой
2/4/8 с на 5xx, таймаут и битый JSON. На всплеск Blizzard отвечает коротким
`403` без `Retry-After` (25.10.2026 — после ~1200 запросов за 5 минут при
4 запросах в секунду; через минуту снова `200`), иногда `429`. Тогда все
запросы ждут 30 с, при повторе 60, 120… до 5 минут (или `Retry-After`), темп
вдвое падает до конца запуска (не реже запроса в 2 с), и та же страница
повторяется — до 8 раз и не дольше 15 минут пауз за запуск. После 12 подряд
неудачных страниц запуск останавливается (`stop_reason=upstream_failing`).

Целостность: таблица записывается, только если прочитаны все её страницы, —
частичного снимка не бывает. Неудачная таблица получает `last_error` и
`failures`; после трёх неудач подряд прошлый сезон уходит в конец очереди.

«Текущий» сезон — самый новый в `seasonMetaData` для пары регион × режим.
Когда появляется новый, прежний перечитывается один последний раз с флагом
`final` и больше не читается.

## Хранение

Файл `$HS_API_DATA_DIR/blizzard_leaderboards.db` (WAL). Он отделён от
`hs_parses.db`: живой обход добавляет ~100 000 строк в день, история — несколько
миллионов, а зеркало `hs_parses.db` в PostgreSQL перехеширует таблицы при
каждой синхронизации. Поэтому таблицы лидеров в зеркало не входят, и GraphQL
читает тот же SQLite-файл, что и REST.

| Таблица | Что хранит |
| --- | --- |
| `lb_boards` | Таблица (регион, режим, сезон): `is_latest`, `final`, размер, последний снимок, ошибки |
| `lb_snapshots` | Не больше одного снимка на таблицу и UTC-день (повторное чтение в тот же день заменяет его); `total_size`, `stored_rows`, `truncated`, `final`, рейтинг на местах 1/100/1000/5000 |
| `lb_entries` | Строки снимка: `rank` (ключ), игрок, `rating` |
| `lb_players` | Ник в регионе; `name_key` — casefold для поиска |
| `lb_runs` | Запуски и их сводка |

Объём: ~35 байт на строку с индексами, ~4 МБ в день на живые сезоны,
~250 МБ на всю историю.

## REST

Все ответы — `{data, meta}`, `meta.source_id = blizzard_leaderboards`.
`mode`: `standard`, `wild`, `battlegrounds`, `battlegroundsduo`, `arena`,
`undergroundarena`, `twist`; `region`: `EU`, `US`, `AP`; `season` — id сезона
Blizzard (по умолчанию самый новый сохранённый).

| Endpoint | Параметры | Что возвращает |
| --- | --- | --- |
| `GET /v1/leaderboards` | `mode`, `region`, `season`, `day` (YYYY-MM-DD), `q` (подстрока ника), `limit` ≤ 100, `offset` | `board`, `snapshot`, `days[]` (дни со снимками), `total`, `rows[]`: `rank`, `name`, `rating`, `previous_rank` (место в предыдущем снимке) |
| `GET /v1/leaderboards/stats` | `mode`, `region`, `season`, `bucket` | `top_rating`; `thresholds[]` — рейтинг на местах 100/1000/5000 и `reached`; `rating` — min/max/mean/median; `histogram[]` (ширина корзины по умолчанию: Поля сражений 500, Подземная арена 250, Арена 0,5); `series[]` — по дням `total_size` и `rating_at_*` |
| `GET /v1/leaderboards/players` | `q` (начало ника, ≥ 2 символа), `region`, `limit` ≤ 50 | Игроки и их места в текущих сезонах |
| `GET /v1/leaderboards/player` | `name`, `region`, `mode` | `seasons[]`: `best_rank`/`best_day`, `last_rank`/`last_rating`/`last_day`, `in_latest_snapshot`, `days`, `series[]` по дням; `shared_name = true`, если под этим ником в одной таблице несколько аккаунтов |
| `GET /v1/leaderboards/seasons` | — | Все режимы, регионы и сезоны, `has_data`, `final`, `is_latest`, `total_size` |
| `GET /v1/leaderboards/coverage` | `runs` | Прогресс по режимам и регионам и последние запуски (без кэша) |

Режимы без рейтинга (Стандарт, Вольный, Twist) отдают `rating = null`, пороги
без рейтинга и `histogram = null`; число игроков есть всегда.

## GraphQL

`leaderboard`, `leaderboardStats`, `leaderboardPlayers`, `leaderboardPlayer`,
`leaderboardSeasons` — JSON той же формы, что `data` соответствующего REST.

## Настройки

| Переменная | По умолчанию | Смысл |
| --- | --- | --- |
| `HS_LEADERBOARDS_REGIONS` | `EU,US,AP` | Регионы |
| `HS_LEADERBOARDS_MODES` | 7 режимов выше | Режимы |
| `HS_LEADERBOARDS_RPS` | 2 | Запросов в секунду на весь запуск |
| `HS_LEADERBOARDS_CONCURRENCY` | 2 | Одновременных запросов |
| `HS_LEADERBOARDS_MAX_PAGES` | 2000 | Страниц на таблицу |
| `HS_LEADERBOARDS_HISTORY_PAGES` | 2000 | Страниц истории за запуск |
| `HS_LEADERBOARDS_LIVE_SECONDS` / `_HISTORY_SECONDS` | 3600 / 1500 | Бюджет времени запуска |

Ручной запуск: `python -m app.cli refresh-blizzard-leaderboards --scope live`
(`--max-boards N` для диагностики).
