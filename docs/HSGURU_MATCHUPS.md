# HSGuru matchup matrices

Источник `hsguru_matchups` собирает матрицы матчапов «архетип против
архетипа» со страницы [HSGuru /matchups](https://www.hsguru.com/matchups) для
каждого публичного среза и отдаёт их одним API с параметрами среза. Он
заменяет подход «отдельный source на комбинацию» (`hsguru_matchups_legend`,
`hsguru_matchups_wild_legend`, `hsguru_matchups_diamond_4to1` пока остаются
для старых потребителей).

## Что отдаёт HSGuru

HSGuru считает матрицы заранее только для `format × rank × period`
(`DeckTracker.aggregated_matchups` в
[d0nkey.top](https://github.com/borisbabic/d0nkey.top)): Standard
пересчитывается каждый час в :51 UTC, Wild — раз в три часа в :23.

- **Форматы:** Standard (`format=2`), Wild (`format=1`).
- **Ранги (меню HSGuru):** `all`, `top_legend` (Top 1k), `top_5k`, `legend`,
  `diamond_to_legend`, `diamond_4to1`, `bronze_plat`.
- **Периоды:** меню HSGuru — `past_3_hours` … `past_30_days`, текущий патч
  `patch_*`, дополнение (`black_empire`) и окно банов (`black_empire_ban`).
  Список берётся из `dimensions.periods` датасета `hsguru_meta_matrix` (меню
  у меты и матчапов общее); период, ушедший из меню, остаётся читаемым, но
  больше не обновляется.
- **Монетка:** не публикуется. `player_has_coin` не входит в агрегат, HSGuru
  уходит в премиум-запрос по сырым играм и отдаёт страницу ошибки «Oops, looks
  like something went wrong» даже с нашей сессией (проверено 2026-10-25 через
  Scrape.do и FlareSolverr). Поэтому собирается только `any_player`, API
  отвечает `422` на `on_coin` и `going_first`.
- **Пороги:** `min_matchup_sample` и `min_archetype_sample` только фильтруют
  отрисованную таблицу. Каждый срез читается с минимальными значениями HSGuru
  (`1` и `1`), а любой порог применяется при запросе.

Каждая ячейка страницы несёт `aria-label="A versus B - 54.2% 1234 games"`,
каждая строка — `aria-label="A - 179032 games"` и общий винрейт архетипа;
общее число игр — `phx-value-total_games`. Пустые ячейки (`0% 0 games`) не
сохраняются.

## Сбор

Таймер `hs-data-api-docker-refresh-hsguru-matchups.timer` запускается каждый
час в :10 (после пересчёта HSGuru в :51) и читает только срезы с истёкшим TTL:

| Период | Standard, ч | Wild, ч |
| --- | --- | --- |
| `past_3_hours`, `past_6_hours` | 1 | 3 |
| `past_day`, окно банов | 2 | 6 |
| `past_3_days` | 6 | 12 |
| `past_week`, патч, дополнение | 12 | 24 |
| `past_2_weeks`, `past_30_days` | 24 | 48 |

Срез считается просроченным за 15 минут до TTL, чтобы часовой таймер не
пропускал часовые окна. Сначала читаются ни разу не прочитанные срезы, затем
самые просроченные, Standard раньше Wild.

**Транспорт:** Scrape.do без браузера (`render=false`, один профиль, 1 кредит,
обычно 1–8 с на страницу 1,5–2 МБ), не больше 5 страниц параллельно (лимит
аккаунта 50, муллиган берёт 40); FlareSolverr — запасной маршрут. Запросы идут
в бюджете Meta-Lab HSGuru как `detail`. Неудачный срез повторяется через 30
минут с удвоением до 12 часов; страница «нет доступа к фильтрам» (нет
публичного агрегата) — через 24 часа. Десять ошибок подряд останавливают запуск.

**Расход:** 140 срезов (2 формата × 7 рангов × 10 периодов), около 800 страниц
в сутки по TTL, то есть ~800 кредитов Scrape.do в сутки и ~24 000 за 30 дней
(Standard ~590, Wild ~210). Первое заполнение — 140 кредитов. Точный план
печатает `python -m app.cli refresh-hsguru-matchups --estimate`, фактический
расход — `meta.last_run` в `/v1/hsguru/matchups/slices`.

Настройки окружения: `HS_HSGURU_MATCHUPS_CONCURRENCY` (1–5, по умолчанию 5),
`HS_HSGURU_MATCHUPS_MAX_PAGES` (по умолчанию 400),
`HS_HSGURU_MATCHUPS_DEADLINE_SECONDS` (по умолчанию 1800).

## Хранение

- SQLite `hsguru_matchup_slices`: один ряд на срез с состоянием очереди
  (`state`, `last_success_at`, `last_changed_at`, `next_attempt_after`,
  `last_error`) и последней матрицей в `payload_json`
  (`archetypes[]` и разреженные `cells[]` вида `[row, col, winrate, games]`).
  Ошибка чтения не стирает последнюю матрицу.
- SQLite `hsguru_matchup_runs`: запуски с числом страниц и кредитов.
- Dataset `hsguru_matchups` — только сводка: измерения, текущий период,
  состояние каждого среза, план расхода и последний запуск.

## API

```bash
curl -s "https://api.kolodahearthstone.com/v1/hsguru/matchups?format=standard&rank=legend&period=black_empire_ban&min_games=25&limit=30"
```

Параметры и поля: [API.md](API.md#hsguru-matchup-matrices). GraphQL:
`hsguruMatchups(format, rank, period, coin, minGames, minArchetypeGames, limit)`
возвращает тот же объект, что `data` REST.
