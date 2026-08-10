# Hearthstone Parses & Data API

Кэширующий парсер и REST API для Hearthstone-источников: HSReplay, HSGuru, Firestone, MetaStats, Hearthstone-Decks, HearthArena и Vicious Syndicate.

Production API:

```text
https://api.hs-manacost.ru
```

Репозиторий:

```text
https://github.com/Zulut30/hearthstone-parses
```

## Что собирает

- **HSReplay**: ranked card stats, Wild/Standard Legend 1 day, Arena cards, Battlegrounds heroes/minions/compositions/trinkets, meta archetypes grouped by class.
- **HSReplay Archetype DB**: локальная SQLite база Standard Legend архетипов: summary, mulligan guide, matchups, popular decks, cards and history snapshots.
- **Vicious Syndicate**: Data Reaper Live class/deck distribution and power tier list, radar graphs.
- **HSGuru**: meta, matchups, streamer decks.
- **Firestone**: Battlegrounds cards/spells/compositions and Arena card stats.
- **MetaStats**: archetypes, decks, matchups.
- **Hearthstone-Decks**: Standard/Wild Legend deck posts with deck codes.
- **HearthArena**: Arena tier list.

## Подробный каталог источников

В реестре **47 источников: 44 scrape + 3 dedicated pipeline**. Авторитетная таблица генерируется напрямую из `app.sources.SOURCES`: [docs/SOURCES.md](docs/SOURCES.md). Проверка синхронизации входит в pytest; обновление после изменения реестра:

Пользовательский каталог «какие данные можно получить, какие поля возвращаются
и какой endpoint выбрать»: **[docs/DATA_CATALOG.md](docs/DATA_CATALOG.md)**.

```bash
python scripts/generate-source-catalog.py
```

Каждый dataset доступен через:

```text
GET /datasets/{source_id}
```

Основные данные лежат в `data.structured`; рядом сохраняются технические поля: `fetched_at`, `backend`, `content_length`, `quality`, `rows_total`, `quality_score`, а при аварийном сохранении прошлого успешного результата - `serving_cached_dataset=true` и `effective_state=ok_cached`.

### HSReplay

HSReplay - главный источник premium/ranked/Battlegrounds/Arena статистики. Для стабильности parser предпочитает API-first каналы (`curl_cffi`, `flaresolverr`) и не сохраняет HTML fallback там, где нужны точные числовые метрики. Premium-страницы используют локальную серверную сессию `HSREPLAY_STORAGE_PATH`; cookie и session values никогда не возвращаются в public API.

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `hsreplay_cards_legend_1d` | Standard cards, Legend, last 1 day | `type=card_stats`, список `cards`: `id`, `dbfId`, `name`, `deck_popularity`, `avg_copies`, `deck_winrate`, `times_played`, `winrate_when_drawn`, `winrate_when_played`, `keep_percentage`, `opening_hand_winrate`, `avg_turns_in_hand`, `avg_turn_played_on`, card metadata. |
| `hsreplay_cards_wild_legend_1d` | Wild cards, Legend, last 1 day | Та же структура `card_stats`, но для Wild: популярность в колодах, winrate, hidden columns, played/drawn/keep/turn metrics. |
| `hsreplay_cards_legend_included_winrate` | Ranked cards, Gold, last 14 days, сортировка по included winrate | `card_stats` с card metrics; используется как более широкий ranked baseline по winrate. |
| `hsreplay_cards_legend_included_popularity` | Ranked cards, Gold, last 14 days, сортировка по included popularity | `card_stats` с card metrics; используется как baseline по популярности. |
| `hsreplay_meta_archetypes_legend_eu_1d` | Meta archetypes, Legend EU, last 1 day | `type=hsreplay_meta_archetypes`, классы и архетипы внутри классов: `class`, `archetype`, `winrate`, `popularity`, `games`, rank/time/region filters. |
| `hsreplay_meta_top_1000_legend_1d_firecrawl` | Meta archetypes, Top 1000 Legend, last 1 day | `type=hsreplay_meta_archetypes`; Firecrawl открывает страницу `/meta/#rankRange=TOP_1000_LEGEND&timeFrame=LAST_1_DAY`, structured-метрики берутся из HSReplay API. Обновляется ежедневно. |
| `hsreplay_meta_legend_1d_firecrawl` | Meta archetypes, Legend, last 1 day | То же для `rankRange=LEGEND`; daily Firecrawl + HSReplay API refresh. |
| `hsreplay_meta_diamond_4to1_1d_firecrawl` | Meta archetypes, Diamond 4-1, last 1 day | То же для `rankRange=DIAMOND_FOUR_THROUGH_DIAMOND_ONE`; daily Firecrawl + HSReplay API refresh. |
| `hsreplay_arena_cards_advanced` | Arena cards, Arenasmith/advanced view | `type=arena_card_tiers`, список `cards`: `name`, `id`, `dbfId`, `arena_class`, `tier`, `deck_winrate`, `win_rate`, `pick_rate`, `offer_rate`, `in_runs`, `avg_copies`, `times_played`, `winrate_when_drawn`, `winrate_when_played`, `score`. Если live API временно недоступен, сохраняется последний полный dataset как `ok_cached`. |
| `hsreplay_arena_class_pages_firecrawl` | Arena class pages for all classes | `type=arena_class_pages`, `classes[]`: class slug/name, `win_rate`, `pct_7_plus`, `pick_rate`, `num_drafts`, Firecrawl status for each class page. Обновляется раз в два дня. |
| `hsreplay_arena_winning_decks` | Arena winning decks | `type=arena_winning_decks`, winning deck runs: class, wins/losses, hero, final deck/cards, deck code/details when available. |
| `hsreplay_arena_legendaries` | Arena legendary groups | `type=arena_legendary_groups`, группы легендарок, key card, related cards, stats/labels для оценки выбора. |
| `hsreplay_arena` | Arena overview | Overview HTML/structured extraction: общие блоки арены, ссылки, tables/json snippets; используется как обзорный источник. |
| `hsreplay_battlegrounds_heroes` | Premium Battlegrounds heroes tier list | `type=bg_heroes`, `heroes`: hero name, `dbfId`, `pick_rate`, `best_comp`, `best_composition_id`, `avg_placement`, `tier`, `placement_distribution`. |
| `hsreplay_battlegrounds_minions` | Premium Battlegrounds minions advanced stats for the current BG patch | `type=bg_minions`, `minions`: minion/card id, name, impact, `win_share`, popularity, tier/card metadata. The query is patch-scoped so a rolling seven-day window cannot mix old and new pools. |
| `hsreplay_battlegrounds_compositions` | Premium Battlegrounds compositions stats | `type=bg_compositions`, comps/tribes: name/type, `first_place`, `avg_placement`, popularity, `placement_distribution`, main/additional cards when available. |
| `hsreplay_battlegrounds_comps` | Battlegrounds comps listing/detail pages | `type=bg_comps`, compositions with `main_cards`, `additional_cards`, minions, source URLs; detail pages are cached to reduce protected-page failures. |
| `hsreplay_battlegrounds_trinkets_lesser` | Battlegrounds lesser trinkets | `type=bg_trinkets`, trinket rows: name, id/card metadata, pick rate and page-derived stats. |
| `hsreplay_battlegrounds_trinkets_greater` | Battlegrounds greater trinkets | Same `bg_trinkets` structure for greater trinkets. |
| `hsreplay_decks_trending` | Trending decks page | `type=trending_decks` or page-structured output: deck links, archetype/title, class, deck cards/code when exposed by page. |

### HSGuru

HSGuru отдаёт meta, matchup matrix и streamer decks. Источник часто защищён Cloudflare, поэтому parser работает через `browser_protected` tier: FlareSolverr primary, browser fallbacks, quality gate по строкам таблиц.

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `hsguru_meta_standard_legend` | Standard meta, Legend | Archetype table: class, archetype/deck name, winrate, popularity, games, rank/format filters. |
| `hsguru_meta_standard_diamond_4to1` | Standard meta, Diamond 4-1 | То же для Diamond 4-1. |
| `hsguru_meta_standard_top_5k` | Standard meta, Top 5K Legend | То же для Top 5K. |
| `hsguru_meta_standard_top_legend` | Standard meta, Top Legend | То же для Top Legend. |
| `hsguru_meta_wild_legend` | Wild meta, Legend | Wild archetypes: winrate, popularity, games. |
| `hsguru_meta_wild_diamond_4to1` | Wild meta, Diamond 4-1 | Wild Diamond 4-1 archetype stats. |
| `hsguru_meta_wild_top_legend` | Wild meta, Top Legend | Wild Top Legend archetype stats. |
| `hsguru_meta_wild_top_5k` | Wild meta, Top 5K | Wild Top 5K archetype stats. |
| `hsguru_matchups_legend` | Matchup matrix, Legend | Matrix/cells by archetype: opponent archetype, matchup winrate, games/sample when available. |
| `hsguru_matchups_diamond_4to1` | Matchup matrix, Diamond 4-1 | Same matchup matrix for Diamond 4-1. |
| `hsguru_streamer_decks_legend_1000` | Streamer decks filtered to top legend | Deck rows: streamer/player, class, archetype, rank, score/date when available, deck link and deck code fill tracking. |

### Firestone

Firestone - public API fallback для Battlegrounds и Arena. Обычно не требует premium auth и даёт агрегированные JSON-like данные.

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `firestone_battlegrounds_comps` | Battlegrounds compositions | `type=bg_comps`, comp name/type, core cards/minions, placement/popularity stats when available. |
| `firestone_battlegrounds_cards` | Battlegrounds minions/cards by tavern tier | BG card rows: name, id, tavern tier, impact/placement/popularity metrics, card metadata. |
| `firestone_battlegrounds_spells` | Battlegrounds spells | Spell rows with tier/type and Battlegrounds performance metrics. |
| `firestone_arena_cards_normal` | Regular Arena card stats | `type=arena_card_tiers`, cards with class, tier/score, winrate/popularity-style metrics. |
| `firestone_arena_cards_underground` | Underground Arena card stats | Same arena card structure for Underground mode. |
| `firestone_arena_legendaries_normal` | Regular Arena legendary cards | Legendary-only arena cards, tier/score and stats. |
| `firestone_arena_legendaries_underground` | Underground Arena legendary cards | Legendary-only Underground arena cards. |

### Vicious Syndicate

Vicious Syndicate используется для Data Reaper Live и radar graphs. Live Beta берётся из Firebase/embedded app data, radars - из deck-library/radar pages.

После крупных обновлений Hearthstone Vicious может временно отдавать только
агрегаты `Other <Class>` или radar предыдущего Data Reaper issue. Live-заглушки
`upstream_unclassified` не публикуются. Последний доступный полноценный radar
публикуется с `upstream_stale` и номерами активного/последнего report issue,
пока на сайте не появится новый radar. Если radar-страницы требуют сессию,
cookies можно импортировать командой
`python -m app.cli vicious-import-storage /path/to/cookies.json`; принимаются
Playwright `storage_state` и экспорт Cookie-Editor.

Docker timer `hs-data-api-docker-refresh-vicious-syndicate.timer` проверяет оба
источника каждые два часа и автоматически заменит issue 349, когда Vicious
опубликует более новый полноценный radar.

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `vicious_syndicate_live_beta` | Data Reaper Live Beta | `type=vicious_live`, class distribution/pie chart, deck distribution, tier list, winrate/power/rank buckets, time ranges, total games when available. |
| `vicious_syndicate_radars` | Data Reaper radar graphs | `type=vicious_syndicate_radars`, radar graph nodes/edges, deck/archetype names, card relationships and source URLs. |

### MetaStats

MetaStats - альтернативный public источник ranked колод и matchup матрицы.

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `metastats_decks` | Archetypes and decks for all classes | Deck/archetype rows: class, archetype, winrate, games, popularity, deck code/list when available. |
| `metastats_matchups` | Archetype matchups | Matchup matrix: archetype vs archetype, winrate, games/sample counts. |

### Hearthstone-Decks

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `hearthstone_decks` | Standard/Wild Legend deck posts | `type=hearthstone_decks`, deck posts: title, class, format, rank/legend placement, score, URL, date, deck code, `deck_code_status`, `missing_deck_code_count`, `deck_code_fill_rate`. Detail pages are retried because deck code may live inside buttons/scripts/text. |

### HearthArena

| Source ID | Что парсим | Какие данные отдаёт |
|-----------|------------|---------------------|
| `heartharena_tierlist` | Arena tier list | `type=heartharena_tierlist`, classes with card rows: card name/id, tier id/label, score/rating and class grouping. |

### Общие гарантии качества данных

- Каждый source получает state из `SourceState`: `ok`, `partial`, `fetch_error`, `http_error`, `blocked_by_protection`, `proxy_required`, `quality_error`, `timed_out`, `never_fetched`. `ok_cached` используется только как вычисляемый `effective_state`, не как сохранённый source state.
- `source_contracts.py` задаёт минимальные строки, обязательные поля, допустимый fallback и regression thresholds для критичных источников.
- `dataset_regression.py` не даёт перезаписать хороший dataset резко уменьшившимся или неполным payload.
- Для premium/anti-bot источников parser сохраняет последний хороший dataset, если live refresh временно упал.
- `/ops/summary` показывает weak sources, DB write failures, cached/preserved datasets, traffic estimate and recent failures.

## Документация

- [REST API](docs/API.md) — endpoints, auth, source IDs, response schemas and examples.
- [HSReplay Archetype Database](docs/HSREPLAY_ARCHETYPE_DATABASE.md) — SQLite schema, refresh CLI, systemd schedule and endpoints.
- [Deploy](DEPLOY.md) — установка, перенос, systemd и runtime checks.
- [Security and Parsing](docs/SECURITY_AND_PARSING.md) — секреты, proxy, premium auth, reliability.
- [Parser Improvement Plan](docs/PARSER_IMPROVEMENT_PLAN.md) — roadmap улучшения стабильности.

## Быстрый старт

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
patchright install chromium

cp .env.example /etc/hs-data-api.env
# Заполните HS_API_KEY, HS_FETCH_PROXY_URL и другие нужные параметры.

python -m app.cli proxy-check
python -m app.cli refresh --all
python -m app.cli refresh-hsreplay-archetypes
python -m app.server
```

На production-сервере используйте `scripts/install.sh` и systemd units из `systemd/`.

## Тесты

Dev/test зависимости отделены от production image:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

`httpx2` используется только TestClient-тестами в соответствии с актуальным
Starlette API; runtime HTTP-клиенты продолжают использовать `httpx`.

## Основные API endpoints

Public:

- `GET /health` — лёгкий liveness.
- `GET /sources` and `GET /sources/{source_id}` — source registry and statuses.
- `GET /datasets` and `GET /datasets/{source_id}` — cached parser output.
- `GET /demo/overview`, `GET /demo/view/{source_id}` — prepared UI payloads.
- `GET /system/technologies` — public parser/source technology overview.
- `GET /api/db/archetypes` and `/api/db/archetypes/{id}` — SQL-backed HSReplay archetype snapshots.
- `GET /ui`, `/ui/logs`, `/ui/technologies` — web UI pages.

Admin/ops, requires `X-API-Key`:

- `POST /admin/refresh`
- `PUT /admin/datasets/{source_id}`
- `GET /ops/health`
- `GET /ops/summary`
- `GET /ops/events`
- `GET /ops/trace/{trace_id}`
- `GET /ops/run/{run_id}`
- `GET /health/premium`

Подробнее: [docs/API.md](docs/API.md).

## Примеры

```bash
curl -s "https://api.hs-manacost.ru/health" | jq .

curl -s "https://api.hs-manacost.ru/sources?site=hsreplay" | jq .

curl -s "https://api.hs-manacost.ru/datasets/hsreplay_meta_archetypes_legend_eu_1d" \
  | jq '.data.structured.classes[0]'

curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "https://api.hs-manacost.ru/ops/health" | jq .
```

## Надёжность

Парсер использует:

- tiered refresh orchestration: `light_api`, `medium_api`, `browser_patchright`, `browser_protected`;
- API-first parsers там, где возможно;
- residential proxy and backend rotation;
- quality gates and dataset regression checks;
- structured schema validation for typed API-first datasets;
- contract fixtures for real upstream payload shapes;
- stale cache preservation when live refresh fails;
- admin-only ops logs, premium auth health and refresh timelines.

## CLI

```bash
python -m app.cli proxy-check
python -m app.cli preflight
python -m app.cli refresh --all
python -m app.cli refresh --source hsreplay_cards_legend_1d
python -m app.cli hsreplay-login
```

## Production Checks

```bash
curl -s "http://127.0.0.1:8000/health" | jq .

curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "http://127.0.0.1:8000/ops/health" | jq .

curl -s -H "X-API-Key: ${HS_API_KEY}" \
  "http://127.0.0.1:8000/health/premium?live=true" | jq .
```

## License

MIT
