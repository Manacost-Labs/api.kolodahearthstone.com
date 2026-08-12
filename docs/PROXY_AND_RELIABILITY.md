# Архитектура парсера, надёжность и ротация IP

## Схема потока данных

```mermaid
flowchart TB
    subgraph trigger [Запуск]
        CLI[app/cli.py refresh]
        Timer[systemd timer]
        API[GET /sources]
    end

    subgraph fetcher [app/fetcher.py]
        Preflight{Route-aware preflight}
        Route{Тип источника}
        APIpath[API-first: Firestone / HSReplay JSON / MetaStats]
        Cloud[Cloud: Scrape.do → Firecrawl → Bright Data opt-in → Scrapfly]
        Browser[Browser path: rotator + patchright / FlareSolverr]
        Quality[publish_gate + contracts + semantic validators]
        Store[app/storage.py]
    end

    subgraph proxy [app/scrapers/proxy.py]
        IPRoyal[IPRoyal residential geo.iproyal.com]
    end

    CLI --> fetcher
    Timer --> fetcher
    fetcher --> Preflight
    Preflight --> Route
    Preflight -. только если без proxy нет полезного маршрута .-> IPRoyal
    Route -->|zerotoheroes, analytics| APIpath
    Route -->|protected cloud-capable| Cloud
    Route -->|hsguru, hsreplay HTML| Browser
    APIpath -. последний route-specific fallback .-> IPRoyal
    Browser -. после proxyless backends .-> IPRoyal
    APIpath --> Quality
    Cloud --> Quality
    Browser --> Quality
    Quality --> Store
```

## Уровни защиты (что уже есть)

| Слой | Механизм | Файл |
|------|----------|------|
| Residential fallback | `HS_FETCH_REQUIRE_PROXY=true` сохраняет IPRoyal для разрешённых proxy-backed маршрутов; независимые proxyless/cloud-маршруты выполняются раньше | `proxy.py`, `config.py`, `fetch_routes.py` |
| Route-aware preflight | Proxy/FlareSolverr блокируют только selection без независимого полезного маршрута | `fetch_routes.py`, `preflight.py` |
| Cloud fallback | Scrape.do → Firecrawl → Bright Data opt-in → Scrapfly | `firecrawl_backend.py`, `brightdata_backend.py` |
| Ротация бэкендов | HSGuru: FS → patchright → scrapling → curl → cloudscraper; cap `HS_FETCH_BACKEND_MAX_SECONDS` | `rotator.py` |
| Stale Telegram | После `refresh --all`: `stale_ok` если status ok, но данные старше `HS_STALE_HOURS` | `stale_monitor.py` |
| Jitter между **браузерными** источниками | 8с × random(0.75–1.25) (`HS_REFRESH_DELAY_BROWSER_ONLY=true`) | `fetcher.py` |
| Параллель API-тиров | light_api до 5, medium_api до 2 + stagger 0.3–1.0с | `fetcher.py`, `source_tiers.py` |
| User-Agent | 6 вариантов Chrome, хэш от `source.id` | `browser_pool.py` |
| Publish gate | Backend policy, contract, semantic quality и regression guard перед сохранением | `publish_gate.py`, `source_contracts.py`, `source_validators.py` |
| HSReplay relogin | Авто-перелогин при истечении Premium-сессии | `fetcher.py`, `hsreplay_auth.py` |
| Telegram | Алерт при `fetch_error`, `quality_error`, CF block | `fetcher.py` |
| FlareSolverr | Отдельная browser-сессия **на каждый источник** (по умолчанию) | `fetcher.py` |

## Ротация IP (IPRoyal)

### Режимы (`/etc/hs-data-api.env`)

| Переменная | По умолчанию | Эффект |
|------------|--------------|--------|
| `HS_PROXY_STICKY_MODE` | `domain` | **Рекомендуется:** один IP на `hsguru.com` / `hsreplay.net` (снижает day-2 баны) |
| `HS_IPROYAL_SESSION_PER_SOURCE` | `false` | `user_session-SOURCE_ID` — липкий IP **на каждый source_id** (часть тарифов даёт **407**) |
| `HS_IPROYAL_ROTATE_PER_FETCH` | `false` | Новый `_session-<random>` на **каждый** запрос — только для отладки |
| `HS_HTTP_RETRY_ATTEMPTS` | `3` | HTTP retries с backoff 5s → 15s → 45s + burn сессии при 403/401/429 |
| *(оба false)* | **текущий прод** | Rotating residential: **новый IP на новое TCP-соединение** |
| `HS_FLARESOLVERR_SESSION_PER_SOURCE` | `true` | Новый браузер FlareSolverr на каждый source в `refresh` |

### Проверка ротации

```bash
python -m app.cli proxy-check              # один IP + краткий rotation sample
python -m app.cli proxy-rotation-check     # 8 выборок, список unique_ips
```

Если `unique_ips` = 1 при rotating-тарифе — включите `HS_IPROYAL_ROTATE_PER_FETCH=true` **или** уточните у IPRoyal, что порт 12321 — rotating, не static.

### Важно

- **Firestone / zerotoheroes** раньше ходили **мимо прокси** — исправлено: все `httpx` через `httpx_client_kwargs()` + `max_keepalive_connections=0`.
- **Patchright**: новый browser context на каждый fetch (изоляция cookies + proxy).
- Прямые API (без браузера): ~15 источников — быстрее и стабильнее, чем HTML.

## Фазы `refresh --all` (ускорение без ослабления CF-защиты)

Порядок жёстко задан в [`app/source_tiers.py`](../app/source_tiers.py) и [`app/fetcher.py`](../app/fetcher.py):

```mermaid
flowchart TB
    locks[Persistent per-source ResourceLockSet in .locks]
    locks --> p1[Phase1 light_api parallel max 5]
    p1 --> p2[Phase2 medium_api parallel max 2]
    p2 --> p3[Phase3 browser_patchright serial]
    p3 --> p4[Phase4 browser_protected serial + FlareSolverr]
```

| Тир | Источников | Параллель | Пауза 8с |
|-----|------------|-----------|----------|
| `light_api` | 15 | до `HS_REFRESH_PARALLEL_LIGHT` (5) | нет |
| `medium_api` | 2 | до `HS_REFRESH_PARALLEL_MEDIUM` (2) | нет |
| `browser_patchright` | 2 (HSReplay Gold cards) | **1** | да |
| `browser_protected` | 14 (HSGuru + HSReplay HTML) | **1** | да |

**Не параллелим:** HSGuru, FlareSolverr, общий Patchright pool, два `hsreplay_cards_*` одновременно.

Откат к почти последовательному режиму без смены кода: `HS_REFRESH_PARALLEL_LIGHT=1`, `HS_REFRESH_PARALLEL_MEDIUM=1`.

В логах: `refresh phase=light_api duration=... ok=... fail=...`.

## Надёжность по типам источников

| Группа | Источники | Backend | Стабильность |
|--------|-----------|---------|--------------|
| API JSON | Firestone BG/Arena, HSReplay arena, MetaStats, Hearthstone-decks, vS radars | `*_api` | Высокая |
| Browser + API | HSReplay Gold cards (`card_list`) | patchright + перехват API | Высокая |
| Browser | HSGuru meta/matchups | FlareSolverr (+ scrapling fallback) | Средняя (CF) |
| API/HTML | HSReplay BG comps | FlareSolverr или curl_cffi HTML/markdown (`battlegrounds_comps_parse.py`) | Средняя |
| Browser | HSReplay trinkets/trending | FlareSolverr / patchright | Средняя |
| HTML parse | HearthArena tierlist | Scrape.do-first cloud cascade → residential fallback | Высокая |

## Stealth-бэкенды и lab-режим

| Backend | Cron (`HS_FETCH_BACKENDS`) | Lab (`--lab-backends` / `HS_FETCH_BACKENDS_LAB`) |
|---------|---------------------------|--------------------------------------------------|
| FlareSolverr | да | да |
| Scrapling | fallback | да |
| Patchright | да | да |
| curl_cffi / cloudscraper | да | да |
| CloakBrowser | **нет** (headed, нестабилен на HSGuru) | да |

```bash
# Эксперимент с CloakBrowser на одном источнике:
/srv/hs-data-api/venv/bin/python -m app.cli refresh --lab-backends --source hsguru_meta_standard_legend
```

## HSReplay каналы

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `HS_HSREPLAY_JSON_CHANNELS` | `flaresolverr,scrape_do,curl_cffi` | JSON API: бесплатный solver → Scrape.do → residential fallback |
| `HS_HSREPLAY_MARKDOWN_CHANNELS` | `flaresolverr,curl_cffi` | Markdown listing/detail; **без jina** (451) |

BG comps: markdown с валидацией `_markdown_body_usable`; при провале — `fetch_hsreplay_html` + `extract_bg_comps`.

## Рекомендуемые настройки продакшена

```env
HS_FETCH_REQUIRE_PROXY=true
HS_FETCH_DIRECT_ENABLED=false
HS_API_REQUEST_DELAY_SECONDS=8
HS_FETCH_MAX_RETRIES=3
HS_FETCH_BACKEND_MAX_SECONDS=240
HS_IPROYAL_SESSION_PER_SOURCE=false
HS_IPROYAL_ROTATE_PER_FETCH=false
HS_FLARESOLVERR_SESSION_PER_SOURCE=true
HS_FETCH_BACKENDS=flaresolverr,patchright,scrapling,curl_cffi,cloudscraper
HS_HSGURU_FETCH_BACKENDS=flaresolverr,patchright,scrapling,curl_cffi,cloudscraper
HS_FETCH_BACKENDS_LAB=cloakbrowser,flaresolverr,scrapling,patchright,curl_cffi,cloudscraper
HS_HSREPLAY_JSON_CHANNELS=flaresolverr,scrape_do,curl_cffi
HS_HSREPLAY_MARKDOWN_CHANNELS=flaresolverr,curl_cffi
HS_REFRESH_PREFLIGHT_STRICT=true
HS_REFRESH_PARALLEL_LIGHT=3
HS_REFRESH_PARALLEL_MEDIUM=2
HS_REFRESH_DELAY_BROWSER_ONLY=true
HS_STALE_HOURS=12
```

Очистка устаревших status-файлов без источника в `SOURCES`:

```bash
/srv/hs-data-api/venv/bin/python /srv/hs-data-api/scripts/cleanup-orphan-statuses.py
```

При частых 429/403 на одном IP: сначала увеличьте delay до 12–15с; затем попробуйте `HS_IPROYAL_ROTATE_PER_FETCH=true` (если IPRoyal не отвечает 407).

## Слабые места (мониторить)

1. **HSGuru** — FlareSolverr primary; Scrapling медленный (до 240 с cap).
2. **HSReplay comps** — HTML + enrichment detail pages чувствительны к изменению
   upstream-разметки и требуют semantic validation.
3. **Один FlareSolverr контейнер** — SPOF для локальных FlareSolverr-only
   selections; cloud-capable источники могут использовать provider fallback.
4. **HSReplay Premium cookie** — один `hsreplay-auth.json` на browser HSReplay.
5. **Orphan statuses** — файлы в `statuses/` без `source_id` в `SOURCES` (удалять скриптом выше).

## Структурированные логи (JSONL)

Файл: `{HS_API_DATA_DIR}/logs/refresh-events.jsonl`

Каждая строка — JSON с полями:

| Поле | Описание |
|------|----------|
| `action` | Пошаговое действие, напр. `browser.backend.try`, `api.route.fail` |
| `action_group` | Группа: `browser`, `api`, `http`, `proxy`, `quality`, … |
| `level` | `info` / `warn` / `error` |
| `trace_id` | Корреляция всех шагов одного источника |
| `run_id` | Один полный `refresh --all` |
| `step` | Порядковый номер шага в trace |
| `source_id`, `backend`, `http_status`, `url`, `bytes`, `attempt`, `duration_ms` | Контекст |

Панель: `/ui/logs` · API: `/ops/summary`, `/ops/events`, `/ops/trace/{trace_id}`, `/ops/run/{run_id}`

Дублирование в journalctl: строки `[error|warn|info] action source=…`.

Ротация управляется переменными `HS_LOG_ROTATE_MAX_BYTES` и
`HS_LOG_ROTATE_MAX_AGE_DAYS`. Сжатые архивы автоматически удаляются по двум
ограничениям: `HS_LOG_RETENTION_DAYS` (по умолчанию 14 дней) и
`HS_LOG_RETENTION_ARCHIVES` (по умолчанию пять последних архивов).

## Runbook

### Несколько источников в `fetch_error` после cron

1. Откройте `/ui/logs?api_key=YOUR_HS_API_KEY` — фильтр «только проблемные».
2. Сводка `/ops/summary` (с заголовком `X-API-Key`): поля `stale_datasets`, `hsreplay_auth`.
3. Точечный refresh (последовательно, без шторма):

```bash
HS_REFRESH_PARALLEL_LIGHT=1 /srv/hs-data-api/venv/bin/python -m app.cli refresh \
  --source SOURCE_ID_1 --source SOURCE_ID_2
```

### HSReplay API 403 / `api.route.fail`

1. `python -m app.cli preflight` — проверить proxy и каналы (`HS_HSREPLAY_JSON_CHANNELS`).
2. Убедиться, что FlareSolverr запущен: `systemctl start hs-flaresolverr`.
3. Проверить `hsreplay-auth.json` (возраст >7 дней → `hsreplay-login`).

### FlareSolverr down

Строго FlareSolverr-only selection завершится ошибкой. API-first и
cloud-capable источники не должны блокироваться глобальным preflight и могут
продолжить по независимому маршруту. Для восстановления локального browser path:
`docker compose -f /srv/hs-data-api/docker-compose.yml restart flaresolverr`.

### Proxy 407

Остановите `HS_IPROYAL_ROTATE_PER_FETCH` / `HS_IPROYAL_SESSION_PER_SOURCE` и
проверьте credentials/баланс. `407` фиксируется как ошибка конкретного source и
не прерывает независимые источники всей `light_api` фазы; cloud/browser fallback
для этого source используется только если он разрешён его route policy.

## Команды диагностики

```bash
./scripts/audit.sh
python -m app.cli preflight --strict
python -m app.cli proxy-rotation-check
python -m app.cli refresh --source hsreplay_cards_legend_included_popularity
curl -s http://127.0.0.1:8000/health | jq .
curl -s -H 'X-API-Key: YOUR_KEY' 'http://127.0.0.1:8000/ops/summary?since_hours=6' | jq .
```
