# HSGuru: Потасовка и киберспорт

Четыре pipeline-источника собирают с HSGuru то, чего не было в других наборах:
статистику текущей Потасовки, турниры с сетками и составами, очки HSEsports и
архив Masters Tour. Поля наборов и задачи «как получить» описаны в
[DATA_CATALOG.md](DATA_CATALOG.md#hsguru-потасовка-и-киберспорт), параметры и
примеры ответов REST — в [API.md](API.md#hsguru-brawl-and-esports), GraphQL — в
[GRAPHQL_API.md](GRAPHQL_API.md#hsguru-brawl-and-esports). Таблица аудита
всех разделов HSGuru — в [DATA_CATALOG.md](DATA_CATALOG.md#аудит-покрытия-hsguru).

## Источники и расписание

| Source ID | Команда | Таймер | Что читает |
| --- | --- | --- | --- |
| `hsguru_brawl` | `refresh-hsguru-brawl` | `hs-data-api-docker-refresh-hsguru-brawl.timer`, каждые 3 ч в :50 | 32 среза `/meta?format=-1&period=brawl` (7 рангов × 3 варианта монетки и 11 классов соперника для `all`, `min_games=10`) и 3 страницы `/decks` Потасовки (`min_games=50`) |
| `hsguru_esports_tournaments` | `refresh-hsguru-esports --part tournaments` | `hs-data-api-docker-refresh-hsguru-esports.timer`, ежедневно в 05:40 | `/esports` (с браузером), `/mt/tour-stops`, для каждого турнира — standings каждой стадии и составы |
| `hsguru_esports_points` | `refresh-hsguru-esports --part points` | тот же | `/leaderboard/points` за каждый сезон × лидерборд (`STD`, `BG_LL`), `limit=3000` |
| `hsguru_esports_legacy` | `refresh-hsguru-esports --part legacy` | тот же, не чаще раза в неделю | `/legacy-hsesports/mt/stats`, `/mt/qualifier-stats/<период>`, `/mt/earnings?points_system=…&season=…` |

Без `--part` команда обновляет все три набора по очереди. `--force-legacy`
читает архив, даже если ему меньше недели; запуск из панели parser control
делает это всегда. С `--scheduled` набор пропускается, если его раздел
(«Потасовка» или «Киберспорт») выключен в панели.

Коды выхода `refresh-hsguru-esports`: `0` — все выбранные наборы опубликованы
или законно пропущены, `10` — часть наборов не опубликована, `1` — ни один.

## Публикация и отказы

- Страница, не прошедшая проверку содержимого или не загруженная, попадает в
  `data.structured.acquisition.errors` и в `missing`; запуск продолжается.
- `hsguru_brawl` публикуется, только если прочитан срез `all|any_player|any`.
  Иначе остаётся прошлый снимок, а статус получает
  `serving_cached_dataset: true`. Пропущенные второстепенные срезы дают
  состояние `partial`.
- `hsguru_esports_tournaments` публикуется, если прочитан список тур-стопов.
  Турнир, который начался больше 10 дней назад и был полностью собран позже
  этого срока, повторно не читается (`reused: true`). Неудачное повторное
  чтение незавершённого турнира оставляет его последний полный снимок
  (`refresh_failed: true`).
- `hsguru_esports_points` публикуется при хотя бы одном прочитанном сезоне,
  `hsguru_esports_legacy` — при прочитанной статистике MT.
- Списки сезонов, периодов квалификаций и legacy-сезонов берутся со страниц
  HSGuru, поэтому новые значения появляются без изменения кода.

## Транспорт и расход

Scrape.do: страница без браузера (1 кредит), при неудаче проверки — один
повтор с браузером (5 кредитов), `/esports` — сразу с браузером. Super proxy,
Decodo и платные резервы не используются. Не больше 5 страниц одновременно;
страницы идут как `detail`-задачи общего бюджета HSGuru (Meta-Lab). Расход по
живым прогонам и порядок шагов — в
[SCRAPE_PROVIDERS.md](SCRAPE_PROVIDERS.md#потасовка-и-киберспорт-hsguru).

## Ограничения источника

- **Составы больших турниров.** Страница составов показывает первые 50;
  остальные страницы HSGuru переключает только по сокету LiveView
  (URL-параметры `page`, `limit`, `per_page` игнорируются, клик в браузере
  Scrape.do страницу не меняет, API Battlefy с сервера отвечает 403). У таких
  турниров `lineups_complete: false`, `lineups_total` — полное число составов,
  а `lineup_popularity` посчитана по собранным. На 2026-10-25 это MT 2019–2022
  и крупные комьюнити-кубки; MT 2023–2026 (16 игроков) собраны полностью.
  Standings всех стадий собираются полностью.
- **Ранги Потасовки.** Почти все игры Потасовки HSGuru относит к рангу `all`;
  остальные ранги собираются, но обычно пусты.
- **Название Потасовки** на странице не указано: набор описывает текущую
  Потасовку на момент `fetched_at`.
- **Legacy-сезоны без `season`** (`2020 Earnings`, `2021 Points`, `2022 Season
  Championships`) HSGuru показывает таблицей сезона по умолчанию, поэтому они
  не собираются.
- Винрейты и матчапы архетипов турнира (`/tournament-lineups/…/stats`,
  `/matchups`) и список зарегистрированных приходят по сокету и в наборы не
  входят. Матчапы Потасовки относятся к срезам матчапов.

## Проверка

```bash
.venv/bin/python -m pytest -q tests/test_hsguru_coverage.py
```

Ручной запуск в контейнере:

```bash
docker compose -f /srv/hs-data-api/docker-compose.yml run --rm api python -m app.cli refresh-hsguru-esports --part points
```
