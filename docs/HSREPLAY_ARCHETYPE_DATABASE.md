# HSReplay Archetype Database

Локальная база архетипов хранит регулярные снимки HSReplay Standard Legend
статистики в SQLite (`hs_parses.db`). Она нужна, чтобы API быстро отдавал
summary, mulligan guide, matchups, сборки и историю без повторного live-парсинга
страниц HSReplay на каждый пользовательский запрос.

## Источник данных

Сборщик использует:

- `data/firecrawl/hsreplay-index-latest.json` как каталог Standard архетипов.
- HSReplay analytics endpoints:
  - `archetype_popularity_distribution_stats_v2`
  - `head_to_head_archetype_matchups_v2`
  - `list_decks_by_win_rate_v2`
  - `single_archetype_mulligan_guide_v2`
  - `single_archetype_stats_over_time_v2`
- сохранённую HSReplay session/cookies из `hsreplay-auth.json`.

По умолчанию фильтры такие:

| Поле | Значение |
| --- | --- |
| GameType | `RANKED_STANDARD` |
| LeagueRankRange | `LEGEND` |
| Region | `REGION_EU` |
| Summary/matchups time range | `LAST_7_DAYS` |
| Decks time range | `LAST_30_DAYS` |
| Mulligan time range | `LAST_30_DAYS` |

`LAST_30_DAYS` для mulligan/decks выбран намеренно: так делает фронтенд
HSReplay для бесплатного/обычного отображения вкладок архетипа.

## SQLite tables

Схема создаётся в `app.db.init_db()`.

| Таблица | Назначение |
| --- | --- |
| `archetype_refresh_runs` | Один запуск обновления, его фильтры, состояние и ошибки. |
| `hsreplay_archetypes` | Справочник архетипов: id, имя, slug, class, url. |
| `archetype_snapshots` | Снимок статистики конкретного архетипа в конкретном run. |
| `archetype_matchups` | Матчапы для snapshot. |
| `archetype_mulligan` | Все mulligan строки; `display_row=1` соответствует списку HSReplay на странице. |
| `archetype_decks` | Сборки архетипа: deck id, games, winrate, raw deck list. |
| `archetype_deck_cards` | Карты каждой сборки, включая sideboard. |
| `archetype_time_series` | Popularity/winrate over time для архетипа. |

Важно: база хранит историю снимков. API по умолчанию отдаёт последний успешный
snapshot, а старые снимки остаются для будущих трендов.

## CLI

Полное обновление всех Standard архетипов из derived sitemap index:

```bash
python -m app.cli refresh-hsreplay-archetypes
```

Диагностический прогон первых N архетипов:

```bash
python -m app.cli refresh-hsreplay-archetypes --limit 3
```

`--limit` работает строго в read-only режиме: проверяет получение и разбор
данных, но не создаёт run/snapshot, не меняет status и не публикует JSON.

Параметры:

```bash
python -m app.cli refresh-hsreplay-archetypes \
  --rank-range LEGEND \
  --game-type RANKED_STANDARD \
  --region REGION_EU \
  --summary-time-range LAST_7_DAYS \
  --deck-time-range LAST_30_DAYS \
  --mulligan-time-range LAST_30_DAYS
```

Только после полного успешного обновления без `--limit` экспортируется
компактный JSON:

```text
/var/lib/hs-data-api/datasets/hsreplay_archetypes_db_latest.json
```

## Docker/systemd schedule

Для Docker deployment:

```bash
sudo cp systemd/hs-data-api-docker-refresh-hsreplay-archetypes.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hs-data-api-docker-refresh-hsreplay-archetypes.timer
```

Расписание:

```text
Mon,Thu *-*-* 03:20:00 Europe/Warsaw
```

Это даёт два снимка в неделю. Weekly sitemap map/index через Scrape.do
обновляется отдельно перед понедельничным refresh, чтобы список архетипов
оставался актуальным.

## API

### `GET /api/db/archetypes`

Список последних snapshot по каждому архетипу.

Query:

| Parameter | Default | Описание |
| --- | --- | --- |
| `class_name` | empty | Фильтр по HSReplay class key, например `ROGUE`. |
| `q` | empty | Поиск по имени, slug или exact id. |
| `rank_range` | `LEGEND` | Rank filter. |
| `game_type` | `RANKED_STANDARD` | Game type. |
| `limit` | `100` | 1..500. |
| `offset` | `0` | Pagination offset. |

Пример:

```bash
curl -s "http://127.0.0.1:8000/api/db/archetypes?class_name=ROGUE" | jq .
```

### `GET /api/db/archetypes/{archetype_id}`

Детальный ответ:

- `snapshot`
- `matchups`
- `mulligan` (`display_row=1`, обычно 40 строк)
- `decks`
- `history`

### `GET /api/db/archetypes/{id}/mulligan`

Query:

| Parameter | Default | Описание |
| --- | --- | --- |
| `display_only` | `true` | Если `true`, только строки как на UI HSReplay (`rank <= 40` и исключены технические token dbf ids). |
| `limit` | `40` | Максимум строк. |

### `GET /api/db/archetypes/{id}/matchups`

Query:

| Parameter | Default | Описание |
| --- | --- | --- |
| `min_games` | `0` | Минимальный sample size. |
| `limit` | `100` | Максимум строк. |

### `GET /api/db/archetypes/{id}/decks`

Query:

| Parameter | Default | Описание |
| --- | --- | --- |
| `include_cards` | `false` | Если `true`, добавляет карты каждой сборки. |
| `limit` | `50` | Максимум сборок. |

### `GET /api/db/archetypes/{id}/history`

Возвращает popularity/winrate time series последнего snapshot.

## UI

В `/ui` добавлена кнопка **Архетипы HSReplay**. Интерфейс показывает:

- список архетипов с фильтром по классу и поиском;
- summary KPI;
- display mulligan guide;
- лучшие/худшие matchup от 100 игр;
- популярные сборки и раскрытие карт сборки.

## Проверка

```bash
curl -s "http://127.0.0.1:8000/api/db/archetypes?limit=3" | jq .
curl -s "http://127.0.0.1:8000/api/db/archetypes/856/mulligan?limit=5" | jq .
curl -s "http://127.0.0.1:8000/api/db/archetypes/856/decks?include_cards=true&limit=1" | jq .
```

SQLite sanity check:

```bash
sqlite3 /var/lib/hs-data-api/hs_parses.db "
select 'archetypes', count(*) from hsreplay_archetypes
union all select 'snapshots', count(*) from archetype_snapshots
union all select 'matchups', count(*) from archetype_matchups
union all select 'mulligan', count(*) from archetype_mulligan
union all select 'decks', count(*) from archetype_decks
union all select 'deck_cards', count(*) from archetype_deck_cards;
"
```
