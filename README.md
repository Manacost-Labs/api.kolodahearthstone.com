# Hearthstone Parses & Data API

[![tests](https://github.com/Zulut30/hearthstone-parses/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/Zulut30/hearthstone-parses/actions/workflows/tests.yml)

Кэширующий парсер и REST API для статистики Hearthstone. Сервис собирает данные
из HSReplay, HSGuru, Firestone, MetaStats, Hearthstone-Decks, HearthArena и
Vicious Syndicate, проверяет их качество и публикует нормализованные JSON-срезы.

- Production API: <https://api.hs-manacost.ru>
- Репозиторий: <https://github.com/Zulut30/hearthstone-parses>
- Каталог данных: [docs/DATA_CATALOG.md](docs/DATA_CATALOG.md)
- Полная документация API: [docs/API.md](docs/API.md)

> `GET /health` проверяет только доступность API. Он не гарантирует, что все
> парсеры успешно обновились и данные свежие. Для полной проверки используйте
> `freshness-check`, `quality-check` и `/ops/summary`.

## Что находится в системе

В текущем реестре **96 источников: 92 scrape + 4 dedicated pipeline**.
Авторитетный список генерируется из `app.sources.SOURCES` и хранится в
[docs/SOURCES.md](docs/SOURCES.md). Ручной перечень здесь намеренно не
дублируется: синхронизацию каталога проверяет pytest.

Основные группы данных:

- Constructed: карты, колоды, архетипы, мета, матчапы и история срезов.
- Battlegrounds: герои, существа, составы, аксессуары и детальная статистика.
- Arena: карты, классы, легендарные группы, winning decks и tier lists.
- Vicious Syndicate: Data Reaper Live и radar-графы.
- Производные наборы: fun/off-meta decks, SQL-индексы и patch-aware snapshots.

Каждый зарегистрированный набор доступен через:

```text
GET /datasets/{source_id}
```

Для новых интеграций предпочтительны типизированные `/v1/*` endpoints и поле
`data.structured`. Практический выбор endpoint и описание полей находятся в
[каталоге данных](docs/DATA_CATALOG.md).

## Архитектура

```mermaid
flowchart LR
    Trigger["Trigger.dev: подготовлен, расписание не включено"] --> Orchestrator["Scoped API + idempotent local queue"]
    Orchestrator --> Start["systemd timer / CLI / admin API / local queue"]
    Start --> Lock["ResourceLockSet для поддерживаемых writers"]
    Lock --> Route{"Маршрут источника"}
    Route --> API["API-first: JSON / curl_cffi / специализированный клиент"]
    Route --> Cloud["Shared cloud scrape: Scrape.do → Firecrawl → Bright Data (opt-in) → Scrapfly"]
    Route --> Browser["Local browser path: FlareSolverr / Scrapling / Patchright"]
    API --> Normalize["Нормализация и схема"]
    Cloud --> Normalize
    Browser --> Normalize
    Normalize --> Gate["Contracts + semantic checks + regression gate"]
    Gate --> AI["Optional Gemma review: observe / quarantine"]
    AI --> Store["JSON cache + SQLite + last-known-good"]
    Store --> REST["REST API / UI / consumers"]
    Start --> Jobs["Deadline + progress snapshots + job-runs"]
    Jobs --> Lock
```

Ключевые компоненты:

| Компонент | Назначение |
| --- | --- |
| `app/sources.py` | Реестр источников, тип и допустимая свежесть. |
| `app/fetcher.py` | Оркестрация refresh, маршрутизация, сохранение последнего хорошего результата. |
| `app/fetch_routes.py`, `app/preflight.py` | Определение доступных маршрутов и preflight только для реально обязательных зависимостей. |
| `app/firecrawl_backend.py` | Общая политика protected-page провайдеров. |
| `app/brightdata_backend.py`, `app/brightdata_state.py` | Выключенный по умолчанию Web Unlocker fallback, локальный guard расходов и circuit breaker. |
| `app/publish_gate.py` | Единая точка проверки кандидата перед публикацией. |
| `app/source_contracts.py` | Минимальные объёмы, обязательные поля и backend policy. |
| `app/dataset_regression.py` | Защита от резкого уменьшения или деградации набора. |
| `app/ai_review.py` | Опциональная семантическая проверка кандидата через OpenRouter со строгой JSON-схемой и безопасным fail-open. |
| `app/reliability_telemetry.py` | Append-only учёт наблюдаемых fresh/LKG/failure исходов основного refresh и окон 24h/7d/30d. |
| `app/resource_locks.py` | Неблокирующие межпроцессные lock-файлы по ресурсам. |
| `app/job_run.py` | Дедлайны, прогресс и атомарные snapshots длительных заданий. |
| `app/storage.py`, `app/db.py` | JSON snapshots, резервные копии и SQLite/WAL. |
| `app/main.py` | REST API, admin/ops endpoints и web UI. |

## Политика провайдеров

Специализированные API-first маршруты используются там, где источник отдаёт
структурированные данные. Защищённые страницы могут идти через локальный
browser rotator или через общий cloud-scrape каскад. Для общего cloud-маршрута
порядок фиксирован:

1. **Scrape.do** — основной платный провайдер. Временные `429/5xx` получают
   ограниченный retry; Cloudflare challenge может переключить запрос на Super.
2. **Firecrawl** — первый резерв с ротацией настроенных ключей.
3. **Bright Data Web Unlocker** — платный слой только для явно
   разрешённых public HTTPS-источников. Он выключен по умолчанию и не делает
   запросов, пока одновременно не заданы флаг включения, API key, Web Unlocker
   zone, точный allowlist source ID и месячный лимит больше нуля.
4. **Scrapfly** — финальный резерв с отдельной ротацией ключей.

Переход к следующему провайдеру происходит после исчерпания ограниченных попыток
текущего: внутри Scrape.do возможны retry/Super escalation, а Firecrawl и
Scrapfly могут ротировать ключи. Каждый HTTP 2xx-кандидат проходит общую
проверку статуса, challenge-маркеров, размера и принадлежности целевому сайту;
неверная страница не останавливает каскад. Для JSON-source применяется его
специализированный validator вместо HTML-эвристики. Bright Data не принимает в
этом каскаде cookies/custom headers, screenshot- и map-запросы. Ключи, cookies
и URL с токенами очищаются из ошибок и не должны попадать в логи. Детали,
ограничения и переменные конфигурации:
[docs/SCRAPE_PROVIDERS.md](docs/SCRAPE_PROVIDERS.md).

## AI-проверка кандидатов

Опциональный слой использует `google/gemma-4-26b-a4b-it` через OpenRouter только
после успешных детерминированных проверок. Модель не может превратить провал
contracts/semantic/regression gate в успех. В `observe` её вердикт записывается
в телеметрию, но не влияет на публикацию; `quarantine` разрешено включать только
после проверки точности shadow-режима.

Во внешний запрос не передаются сырой HTML, тексты полей, URL, cookies, headers,
deck codes или токены. Модель получает ограниченные структурные признаки,
результат локальных проверок и агрегаты качества. Ответ принимается только при
полной строгой JSON-схеме и `finish_reason=stop`. Ошибка, timeout или отсутствие
OpenRouter не останавливают парсер; после трёх последовательных ошибок circuit
breaker отключает AI до следующего refresh. Лимиты источников, параллелизма,
времени, токенов и запросов задаются переменными `HS_AI_REVIEW_*` из
`.env.example`. Пустой `HS_AI_REVIEW_SOURCE_IDS` не делает запросов; все
источники разрешаются только явным значением `*`.

## Гарантии устойчивости

- Generic refresh и HSGuru matrix используют lock по конкретным ресурсам,
  поэтому занятый источник не должен блокировать независимые источники.
- Preflight учитывает выбранный маршрут: proxy или FlareSolverr становятся
  блокирующей проверкой только тогда, когда без них нет полезного маршрута для
  всей выбранной job. API-first, cloud-capable и независимые источники в
  смешанной выборке не останавливаются из-за чужой зависимости.
- Кандидат проходит структурную схему, source contract, semantic validation и
  regression gate до записи.
- После игрового патча ограниченный `early`-режим принимает меньший, но
  проверенный набор только для Arena, HSGuru meta/matchups и HSReplay
  `*_patch`. Такие данные помечаются `provisional`, не заменяют стабильный
  baseline/LKG, а после `earlyUntil` автоматически снова проходят обычные
  пороги. Срезы HSReplay `1d`/`3d`/`7d`/`14d` не ослабляются.
- При временном отказе upstream валидный предыдущий snapshot остаётся доступен
  как `effective_state=ok_cached`; причина последнего сбоя остаётся видимой.
- Последовательная JSON-запись использует временный файл и atomic replace;
  предыдущие datasets и statuses сохраняются в ограниченной ротации backups.
- HSGuru meta matrix имеет отдельный 30-секундный heartbeat, hard deadline
  60 минут и единый lock для matrix refresh и присоединения deck catalog. При
  дедлайне неполный candidate и его history не публикуются, а last-known-good
  остаётся доступен; systemd даёт процессу 65 минут и 30 секунд на остановку.
- Ошибка best-effort telemetry не прерывает сам парсер.
- Состояния источника типизированы: `ok`, `partial`, `fetch_error`,
  `http_error`, `blocked_by_protection`, `proxy_required`, `quality_error`,
  `timed_out`, `never_fetched`.

Наличие last-known-good означает, что API продолжает обслуживать потребителей,
но не превращает неудачный refresh в успех. Именно поэтому liveness, freshness
и quality проверяются раздельно.

Для scheduled CLI действует единая классификация результата:

| Код | Смысл | Пример |
| --- | --- | --- |
| `0` | Успешное выполнение. | Все выбранные источники свежие либо section намеренно отключён и job стала no-op. |
| `10` | Управляемая деградация, данные остаются пригодными. | Обслуживается LKG или ресурс уже занят другой job. |
| `1` | Жёсткая ошибка, пригодного результата нет. | Cold-start failure, timeout без LKG или отсутствует ожидаемый source result. |

Scheduled systemd units, для которых определена деградация, объявляют только
`10` через `SuccessExitStatus=10`, поэтому он не превращает управляемую
деградацию в failed unit. Состояние всё равно
видно отдельно как `dataDegraded`/`ExecMainStatus=10` в parser-control runtime.
Для `freshness-check` режим `health` возвращает `1` на stale data, а
`--exit-mode execution` — `10`, если проверка выполнена, но состояние
деградировано.

### Известные ограничения

- Hard deadline HSGuru отменяет координирующую coroutine и запрещает публикацию
  неполного результата, но синхронный provider request, уже запущенный через
  worker thread, может жить до собственного timeout или остановки процесса.
- Heartbeat подтверждает, что job-процесс жив и snapshot обновляется, но сам по
  себе не доказывает прогресс конкретного upstream-запроса.
- Не все dedicated pipeline writers ещё используют `ResourceLockSet`.
- Общий `storage.write_json()` использует одинаковое имя `.tmp` и не рассчитан
  на две одновременные записи одного source без внешнего lock.
- Docker healthcheck проверяет liveness процесса, а не свежесть parser data.
- System-wide list endpoints пока вычисляют ETag через полный проход по JSON
  snapshots, поэтому их нельзя считать дешёвыми high-QPS endpoints.

Эти ограничения учитываются в operational gate ниже и являются следующими
приоритетами усиления системы.

## Хранение данных

Canonical Docker хранит runtime на host в `/srv/hs-data-api/data` и монтирует
его в контейнер как `/var/lib/hs-data-api`:

| Host path | Container path | Содержимое |
| --- | --- | --- |
| `data/datasets/` | `/var/lib/hs-data-api/datasets/` | Опубликованные JSON snapshots. |
| `data/statuses/` | `/var/lib/hs-data-api/statuses/` | Последнее состояние refresh. |
| `data/.locks/` | `/var/lib/hs-data-api/.locks/` | Lock-файлы; активное владение определяется `flock`. |
| `data/job-runs/` | `/var/lib/hs-data-api/job-runs/` | Прогресс длительных jobs. |
| `data/backups/` | `/var/lib/hs-data-api/backups/` | Предыдущие datasets/statuses. |
| `data/baselines/` | `/var/lib/hs-data-api/baselines/` | Regression baselines. |
| `data/publications/` | `/var/lib/hs-data-api/publications/` | Versioned candidate/published/quarantine records. |
| `data/control/` | `/var/lib/hs-data-api/control/` | Parser-control state и lock. |
| `data/firecrawl/`, `data/scrapfly/` | Одноимённые каталоги | Состояние ротации provider keys без самих ключей. |
| `data/brightdata/` | `/var/lib/hs-data-api/brightdata/` | Локальный счётчик billable reservations/requests и состояние circuit breaker; без API key. |
| `data/logs/refresh-events.jsonl` | `/var/lib/hs-data-api/logs/refresh-events.jsonl` | Структурированные события. |
| `data/hs_parses.db` | `/var/lib/hs-data-api/hs_parses.db` | SQLite/WAL индексы. |
| `data/parser-telemetry.sqlite3` | `/var/lib/hs-data-api/parser-telemetry.sqlite3` | Append-only исходы refresh для честной статистики; без URL и текстов ошибок. |

Canonical Docker читает секреты из игнорируемого Git файла
`/srv/hs-data-api/.env.docker`. `/etc/hs-data-api.env` относится к legacy host
units/CLI; browser sessions хранятся в закрытых файлах data directory.

## Быстрый старт через Docker

Требуются Docker и Docker Compose:

```bash
git clone https://github.com/Zulut30/hearthstone-parses.git
cd hearthstone-parses

cp .env.example .env.docker
chmod 600 .env.docker
# Заполните .env.docker своими ключами и настройками proxy.

docker compose up --build -d
curl -fsS http://127.0.0.1:18081/health
```

API может стартовать без запуска полного парсинга. Не выполняйте
`refresh --all`, пока не настроены proxy, provider keys и необходимые browser
sessions.

## Локальная разработка

Основное приложение работает на Python 3.12. Host-side exporter таймеров имеет
отдельный smoke-test на Python 3.11.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python -m patchright install chromium

python -m pytest -q
python scripts/generate-source-catalog.py --check
```

Запуск API с локальным data directory:

```bash
HS_API_DATA_DIR="$PWD/data" \
HS_API_BIND_HOST="127.0.0.1" \
python -m app.server
```

## Команды оператора

На canonical Docker production выполняйте CLI внутри активного контейнера.
Raw `python -m app.cli` на host всегда пытается загрузить
`/etc/hs-data-api.env` и может неожиданно обратиться к production data.
Команды `proxy-check`, `preflight`, `canary`, `refresh` и dedicated refresh
обращаются к upstream и при fallback могут расходовать лимиты платных
провайдеров.

```bash
# Безопасность маршрута до источников и обязательные зависимости
docker exec hs-data-api python -m app.cli proxy-check
docker exec hs-data-api python -m app.cli preflight --strict
docker exec hs-data-api python -m app.cli canary --strict

# Один источник; non-zero, если свежий набор не опубликован
docker exec hs-data-api python -m app.cli refresh \
  --source hsreplay_cards_legend_1d \
  --require-all-ok

# Состояние уже сохранённых данных без платного refetch
docker exec hs-data-api python -m app.cli freshness-check --since-hours 48
docker exec hs-data-api python -m app.cli quality-check

# Длительный HSGuru pipeline
docker exec hs-data-api python -m app.cli refresh-hsguru-meta-matrix --concurrency 2
```

Scheduled команды могут завершиться кодом `10`; это не свежий успех, а
обслуживаемая деградация. Для ручного health gate не маскируйте этот код и
проверяйте `freshness-check` в режиме `health`.

## Внешняя оркестрация Trigger.dev

В репозитории подготовлен внешний control plane в
[`orchestration/triggerdev/`](orchestration/triggerdev/README.md). Trigger.dev
не получает provider keys, cookies или доступ к data directory: он передаёт
только `sourceIds`/`sectionIds` в два узких endpoint, а фактический refresh
выполняет durable parser-control queue на сервере.

Интеграция ещё не означает, что production-расписание включено. Canary для
Vicious Syndicate намеренно не имеет declarative cron. При вводе в эксплуатацию
сначала отключите соответствующий systemd timer, оставьте service как rollback,
затем включайте только одну canary schedule. Одновременный запуск Trigger.dev и
systemd для одной выборки запрещён.

Для границы доверия используется отдельный `HS_ORCHESTRATOR_API_KEY` длиной не
менее 32 символов; он не должен совпадать с `HS_API_KEY`. В Trigger.dev тот же
секрет хранится как `PARSER_ORCHESTRATOR_TOKEN`. Повтор transient HTTP/network
запроса безопасен: Trigger отправляет стабильный `requestId`, а локальная
очередь идемпотентно возвращает ранее созданный run.

На production команды запускаются внутри штатного venv/container и через
systemd units из `systemd/`. Полная установка и расписание описаны в
[DEPLOY.md](DEPLOY.md).

## Как проверять стабильность

Минимальный production gate состоит из четырёх независимых проверок:

```bash
# 1. API отвечает
curl -fsS http://127.0.0.1:18081/health | jq .

# 2. Нет stale и cached-after-failure источников
docker exec hs-data-api python -m app.cli freshness-check --since-hours 48

# 3. Все cached datasets проходят contracts и quality threshold
docker exec hs-data-api python -m app.cli quality-check

# 4. Фоновые задания не завершились ошибкой
! systemctl --failed --no-pager --no-legend | grep -q hs-data-api
```

Для подробной диагностики с `X-API-Key`:

```bash
curl -fsS -H "X-API-Key: ${HS_API_KEY}" \
  http://127.0.0.1:18081/ops/health | jq .

curl -fsS -H "X-API-Key: ${HS_API_KEY}" \
  http://127.0.0.1:18081/ops/summary | jq .
```

Зелёный `/health` при non-zero `freshness-check` означает: API доступен, но
часть данных устарела. Это деградация parser layer, а не исправное состояние
всей системы.

## Основные endpoints

Public:

- `GET /health` — liveness API.
- `GET /sources`, `GET /sources/{source_id}` — реестр и статусы.
- `GET /datasets`, `GET /datasets/{source_id}` — cached parser output.
- `GET /v1/constructed/*`, `/v1/bg/*`, `/v1/arena/*` — типизированные API.
- `GET /v1/system/sources`, `/v1/system/datasets`, `/v1/system/health` —
  системные read-only представления.
- `GET /v1/system/parsing-reliability` — fresh success, provisional, LKG и
  failures основного generic refresh за 24 часа, 7 и 30 дней со статусом
  `collecting/observed`. Четыре dedicated pipeline пока явно перечислены в
  `methodology.limitations` и не смешиваются с этим процентом.
- `GET /system/technologies` — публичное описание parser stack без секретов.
- `GET /ui`, `/ui/logs`, `/ui/technologies` — встроенный интерфейс.

Admin/ops, требует `X-API-Key`:

- `POST /admin/refresh`
- `PUT /admin/datasets/{source_id}`
- `GET /ops/health`
- `GET /ops/summary`
- `GET /ops/events`
- `GET /ops/trace/{trace_id}`
- `GET /ops/run/{run_id}`
- `GET /health/premium`

Scoped orchestration, требует отдельный `X-Orchestrator-Key`:

- `POST /admin/orchestrator/parser-runs` — идемпотентно поставить выбранные
  источники/секции в локальную очередь.
- `GET /admin/orchestrator/parser-runs/{run_id}` — получить только минимальный
  статус конкретного run.

## Документация

- [docs/DATA_CATALOG.md](docs/DATA_CATALOG.md) — выбор endpoint и поля данных.
- [docs/SOURCES.md](docs/SOURCES.md) — генерируемый реестр всех источников.
- [docs/API.md](docs/API.md) — полная REST API документация.
- [docs/SCRAPE_PROVIDERS.md](docs/SCRAPE_PROVIDERS.md) — Scrape.do, Firecrawl,
  opt-in Bright Data и Scrapfly.
- [orchestration/triggerdev/README.md](orchestration/triggerdev/README.md) — безопасный rollout внешнего control plane.
- [docs/HSREPLAY_ARCHETYPE_DATABASE.md](docs/HSREPLAY_ARCHETYPE_DATABASE.md) — SQL snapshots архетипов.
- [docs/SECURITY_AND_PARSING.md](docs/SECURITY_AND_PARSING.md) — proxy, cookies, auth и threat model.
- [DEPLOY.md](DEPLOY.md) — установка, systemd timers, обновление и recovery.

## Безопасность

- Не коммитьте `.env*`, cookies, storage state, токены и production datasets.
- Не передавайте секреты через query string или публичные endpoints.
- Используйте `HS_FETCH_REQUIRE_PROXY=true` для защищённых production-источников.
- Public API read-only; refresh и ops закрыты `X-API-Key`.
- Для Trigger.dev используйте отдельный случайный токен длиной не менее 32
  символов; не переиспользуйте admin API key.
- Bright Data оставляйте выключенным до настройки точного source allowlist,
  выделенной Web Unlocker zone, ненулевого месячного лимита и однократной
  инициализации usage ledger по текущему значению из provider dashboard.
- HSReplay JSON через Scrape.do работает только как non-rendered fallback для
  точного `https://hsreplay.net`; refresh-scoped request/credit/concurrency
  ceilings обязательны, а CONNECT `402/407` не повторяется через тот же proxy.
- Перед публикацией изменений запускайте tests, Docker build и secret scan.

## License

MIT
