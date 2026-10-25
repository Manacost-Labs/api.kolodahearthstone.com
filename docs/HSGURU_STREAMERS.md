# HSGuru: стримеры, их колоды и кто сейчас в эфире

Источник `hsguru_streamers` (pipeline). Код: `app/hsguru_streamers.py`
(разбор, сбор, чтение) и `app/hsguru_streamers_store.py` (SQLite).

## Что читается

| Страница | Что даёт |
| --- | --- |
| `https://www.hsguru.com/streamer-decks?limit=50&offset=N` | Все колоды стримеров: стример (Twitch ID и ник), колода (`/deck/<id>`, код, архетип, класс), формат, пик/последний/худший ранг Легенды, победы-поражения, время последней игры |
| `https://www.hsguru.com/streaming-now` (+ `?filter_language=ru`) | Кто сейчас в эфире: логин Twitch, название, зрители, длительность (из неё — начало эфира), превью, режим (HSGuru знает его у немногих) |
| `https://www.twitch.tv/<login>` | JSON-LD канала: аватар, описание, фолловеры, ссылки канала (YouTube, X, Discord, Instagram, TikTok, Telegram, VK, Boosty, прочие) и последние прошедшие трансляции с началом и длительностью |

HSGuru отдаёт обе страницы как LiveView dead render: без браузера в HTML уже
есть таблица и карточки. Поэтому страницы идут через Scrape.do без рендера
(1 кредит, около 8 с), а при непрошедшей проверке — с рендером. Параллельно
не больше пяти страниц: у аккаунта Scrape.do 50 одновременных запросов, и 40 из
них берёт муллиган. Twitch читается напрямую: по одному каналу с паузой в
секунду, не больше 30 за запуск. Иногда Twitch отдаёт общую оболочку
приложения без JSON-LD (25.10.2026: 19 из 30 при трёх параллельных, затем 21
из 30 по одному, затем 0 из 20), поэтому страница без профиля читается ещё
раз через 3 секунды, а затем — через два часа.

## Почему один обход вместо всех фильтров

`/streamer-decks` без фильтров — это все форматы (Стандарт, Вольный,
Классика, Твист), все ранги (и без Легенды) и все окна, новые игры сверху,
по 50 строк. Фильтры HSGuru по рангу, окну, формату и классу — подмножества
этого списка, поэтому один обход сверху вниз до отсечки читает их все, а
фильтры применяются у нас:

* первый запуск идёт на `HS_HSGURU_STREAMERS_BACKFILL_DAYS` назад (120 дней —
  самое широкое окно HSGuru, около 300 страниц);
* следующие останавливаются на начале последнего полного обхода минус
  `HS_HSGURU_STREAMERS_OVERLAP_MINUTES` (120), обычно это одна страница.
  Пачки параллельных страниц растут 1, 2, 4, 5, поэтому обычный запуск не
  платит за страницы за отсечкой.

Пока обход идёт, строки только сдвигаются вниз (новые игры встают наверх),
поэтому обход может увидеть строку дважды, но не пропускает. Колода,
сыгранная заново во время обхода, попадает в следующий запуск. Строка — это
стример + колода + формат; повторная игра обновляет её на месте, а старое
чтение не перезаписывает более новую строку. Если страница не прочиталась,
обход не считается полным, и следующий запуск начнёт от прежней отсечки.

## Сейчас в эфире

Каждый запуск заменяет таблицу эфиров целиком (если `/streaming-now`
прочиталась). Эфир привязывается к стримеру по логину или нику. Карточка
эфира даёт настоящий логин Twitch, которого нет у ника не латиницей
(`뀨효미`), и по нему потом читается профиль. Колода эфира — последняя колода
стримера, сыгранная после начала эфира. Если HSGuru не знает режим, режим
берётся из формата этой колоды (`mode_source: "deck"`).

## VOD

Колода, последняя игра которой попала внутрь прошедшей трансляции, получает
`vod.url` с отметкой времени: момент окончания игры минус 5 минут. Колода,
сыгранная в текущем эфире, получает `stream_url` и `on_current_stream: true`.
Twitch показывает последние 5–10 трансляций канала, поэтому у старых колод VOD
нет. Профиль активного стримера перечитывается не чаще раза в 6 часов, если
после прошлого чтения он сыграл новую колоду. Остальных — раз в неделю,
ошибки — через два часа.

## Расписание и бюджет

`hs-data-api-docker-refresh-hsguru-streamers.timer` — каждые 5 минут
(`refresh-hsguru-streamers --scheduled`). Обычный запуск: `/streaming-now`,
`/streaming-now?filter_language=ru` и одна страница колод — около 3 кредитов,
то есть около 860 кредитов в сутки. Первый запуск — около 300 кредитов.
Запросы идут в общий бюджет HSGuru Meta-Lab как `detail`.

Переменные `HS_HSGURU_STREAMERS_*`: `CONCURRENCY` (≤5), `BACKFILL_DAYS`,
`OVERLAP_MINUTES`, `MAX_DECK_PAGES` (400), `PROFILE_LIMIT` (30),
`PROFILE_CONCURRENCY` (1, ≤3), `DEADLINE_SECONDS` (1500), `LANGUAGES` (`ru`,
через запятую; пусто — не читать языковые страницы).

## Хранение

SQLite `hsguru_streamers`, `hsguru_streamer_decks`, `hsguru_streamer_vods`,
`hsguru_streamer_live`, `hsguru_streamer_runs`. Зеркало в PostgreSQL
`analytics.*` — миграция `platform/sql/018_hsguru_streamers.sql`, представление
`hub.hsguru_streamer_decks_latest`. Колоды и стримеры не устаревают; VOD
старше 60 дней и запуски старше 14 дней удаляются.

## REST

| Endpoint | Что возвращает |
| --- | --- |
| `GET /v1/hsguru/streamers` | Стримеры, игравшие за `days` (30; 0 — за всё время): профиль, ссылки, `live`, `window` (колоды, лучший ранг) и `recent_decks` (`decks`, 3). Фильтры `q`, `format`, `player_class`, `language`, `live`; `sort` = `active` (в эфире, потом недавние) / `followers` / `decks` / `rank`; `limit`, `offset`; `meta.count` — всего |
| `GET /v1/hsguru/streamers/{streamer}` | Один стример по логину, нику или Twitch ID: профиль, `live`, `facets` (колоды по формату и классу), `vods`, `last_deck` |
| `GET /v1/hsguru/streamers/{streamer}/decks` | Колоды стримера, новые сверху: `format`, `player_class`, `days`, `limit`, `offset`; ответ с `streamer` |
| `GET /v1/hsguru/streamer-decks` | Колоды всех стримеров: `format`, `player_class`, `archetype`, `max_rank` (пик не хуже), `days` (7), `limit`, `offset` |
| `GET /v1/hsguru/streaming-now` | Эфиры по зрителям: `streamer` (если HSGuru знает его колоды), `current_deck`, `mode`, `mode_source`; фильтры `language`, `mode`, `known`; `meta.observed_at`, `meta.stale` (старше 20 минут); без кэша |
| `GET /v1/hsguru/streamers/coverage` | Сколько стримеров, колод, VOD, эфиров и последние запуски; без кэша |

Колода: `deck_id`, `deck_code`, `archetype` (имя HSGuru), `player_class`
(`PRIEST`, …), `format`, `peak_rank`/`latest_rank`/`worst_rank` (`null` вне
Легенды), `wins`, `losses`, `games`, `winrate`, `last_played_at`,
`hsguru_url`, `on_current_stream`, `stream_url`, `vod`.

Стример: `id` (Twitch user id), `slug` (логин или id — для адреса страницы),
`display_name`, `login`, `twitch_url`, `youtube_url`, `x_url`, `links[]`
(`kind`, `url`), `avatar_url`, `description`, `followers`, `language`,
`decks_total`, `last_played_at`, `hsguru_url`, `live`.

## GraphQL

`hsguruStreamers`, `hsguruStreamer`, `hsguruStreamerDecks`,
`hsguruStreamingNow` — тот же код чтения и та же форма ответа, но из зеркала
PostgreSQL, которое отстаёт от SQLite до 15 минут.
