# Архитектура HS Data API

## Границы проекта

- Канонический Git-репозиторий: `/srv/projects/data/hs-data-api-strategy7`.
- Production working copy: `/srv/hs-data-api`.
- Канонический публичный API: `api.kolodahearthstone.com/v1/` (GraphQL).
- Старые REST-контракты временно доступны на новом домене для поэтапного
  отключения `api.hs-manacost.ru` и `db.kolodahs.ru`.
- Код сначала меняется и проверяется в каноническом репозитории. Production
  обновляется только штатным deployment; ручная правка runtime не считается
  исходником.
- Данные, кеши, cookies и credentials не входят в репозиторий и не должны
  попадать в Git, документацию или тестовые логи.

## Карта каталогов

| Путь | Содержимое | Правило |
| --- | --- | --- |
| `app/` | FastAPI, CLI, реестр источников, orchestration и доменные парсеры. | Основной production-код. |
| `app/routers/` | Версионированные HTTP endpoints и response models. | HTTP-слой не должен содержать scraping-логику. |
| `app/scrapers/` | Общие HTTP/browser backends, resilience и quality helpers. | Повторно используемая инфраструктура сбора. |
| `scripts/` | Установочные, миграционные и совместимые operational entrypoints. | Новую бизнес-логику держать в `app/`, а скрипты оставлять тонкими. |
| `systemd/` | Services и timers для Docker и legacy host install. | Каждой плановой задаче нужны service, timer и тест расписания. |
| `tests/` | Unit, contract и regression tests; `fixtures/` — обезличенные samples. | Реальную сеть и production secrets не использовать. |
| `docs/` | Архитектура, API, каталоги и runbooks. | `SOURCES.md` генерируется скриптом. |
| `config/` | Версионируемая конфигурация без секретов. | Credentials здесь запрещены. |
| `web/` | Встроенный web UI API-сервиса. | Не смешивать с WordPress plugin. |
| `wp-plugins/` | Исходники интеграции WordPress. | Отдельная поверхность сборки и проверки. |
| `lab/` | Экспериментальные backends и прототипы. | Не подключать к production без отдельного решения. |
| `plans/` | Исторические планы реализации. | Актуальные решения переносить в `docs/`. |
| `data/`, `.venv/`, `.codegraph/` | Runtime/local artifacts. | Игнорируются Git и не являются исходниками. |

## Основные потоки

```text
systemd timer / admin API / CLI
              |
              v
       app.cli / app.main
              |
              v
        app.fetcher planner
              |
       +------+-------+
       |              |
 API-first parser   shared page scrape
                      |
                      v
            app.firecrawl_backend
                      |
                      v
          parser -> quality gate
                      |
                      v
       regression gate -> storage/cache
                      |
                      v
             public REST dataset
```

Реестр `app.sources.SOURCES` — единственный источник истины для source IDs.
`scripts/generate-source-catalog.py` строит из него `docs/SOURCES.md`.
`app/source_contracts.py`, quality gate и regression gate не позволяют
неполным upstream-ответам молча заменить хороший dataset.

## Scrape providers

Общий page-scrape должен проходить через `app/firecrawl_backend.py`, а не
вызывать provider API из operational-скрипта. Это даёт одну точку ротации
ключа, sanitization ошибок и учёта credits. Все browser-protected HTML-страницы
и HSReplay sitemap обслуживаются только через Scrape.do; Firecrawl и Scrapfly
не входят в production provider chain. Официальные JSON API остаются прямыми.

## Проверка и deployment

```bash
make setup             # один раз
make provider-check    # быстрый gate provider-слоя
make check             # обязательный полный gate
make security          # отдельный анализ security-рисков
```

После успешных проверок diff проходит ручной review. Только после этого
обновляется `/srv/hs-data-api`, пересобирается Docker image и выполняются
health/smoke checks. Простое изменение файлов working copy не меняет уже
запущенный контейнер, потому что код запечён в image.
