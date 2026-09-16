# Meta Intelligence Lab — отчёт реализации

Дата: 2026-09-16. Ветка: `codex/meta-intelligence-lab`.
Рабочая копия: `/srv/projects/data/hearthstone-parses-meta-lab`.

Реализован закрытый аналитический модуль текущего API. Meta Lab встроен в
основную административную панель `/?action=meta_lab` на корне API-домена, в её
левое меню, систему тем и рабочую область. Старый адрес `/ui/meta-intelligence`
перенаправляет в неё. Доступны выбор формата/ранга/периода/патча, поиск архетипов,
сортировка и фильтрация сборок по выборке, сравнение составов, копирование deck code,
карточные показатели, матчапы, история, сигналы и состояние бюджета/свежести.

## Основание и изоляция

Исходная рабочая копия содержала незакоммиченные изменения, включая необходимые
HSGuru/Scrape.do модули. За основу взят её безопасный снимок поверх
`f0c91ef9d558ed6ebfc935229cd1fe7b810df591`. Чужие изменения не очищались и не
перезаписывались. Поэтому обычный diff к HEAD включает унаследованные изменения;
отдельный patch в `output/meta-intelligence-lab.patch` содержит только эту работу
относительно сохранённого снимка. Для выпуска изменения отдельно перенесены на действующий production baseline
`d0c4fefc2579cd30abe5ccd25cc759a5a4a6985e` в `codex/meta-intelligence-lab-release`.
Существующие publication gates, provider cascade и новые парсеры сохранены.
Первый этап выше проверялся без deployment; порядок текущего выпуска описан ниже.

## Данные и API

[Техническая спецификация](META_INTELLIGENCE_LAB.md) описывает схему SQLite,
политику бюджета, freshness, ограничения статистических выводов, backup/restore,
миграцию и rollback. Основные сущности: attempts, observations, snapshots,
snapshot_attempts, signals, signal_events, jobs и lab_meta.

За существующим `X-API-Key` доступны 10 GET-маршрутов:

- `/v1/meta-intelligence/health`
- `/v1/meta-intelligence/budget`
- `/v1/meta-intelligence/archetypes`
- `/v1/meta-intelligence/archetypes/{archetype}`
- `/v1/meta-intelligence/archetypes/{archetype}/compare`
- `/v1/meta-intelligence/archetypes/{archetype}/timeline`
- `/v1/meta-intelligence/cards`
- `/v1/meta-intelligence/signals`
- `/v1/meta-intelligence/signals/{signal_id}`
- `/v1/meta-intelligence/snapshots/{snapshot_id}`

GET не запускает сбор. Все исследовательские ответы исключены из публичного кеша.
Страница входа не содержит закрытых данных. Ключ хранится только в памяти вкладки.

## Выполненные проверки

Команды запускались в новой рабочей копии. Python для pytest:
`/srv/projects/data/hearthstone-parses/.venv/bin/python`.

| Проверка | Результат |
|---|---|
| `python -m pytest -q --tb=short` | 439 passed, 10 subtests; 4 существующих предупреждения FastAPI on_event |
| `python -m pytest tests/test_meta_intelligence_api.py -q --tb=short` после последнего уточнения API | 14 passed |
| `node --check web/meta-intelligence.js` | PASS |
| `bash -n scripts/export-bundle.sh scripts/import-bundle.sh` | PASS |
| `git diff --check` | PASS |
| Docker build из allowlist-контекста | PASS, `hs-data-api:meta-intelligence-lab-check` |
| Контейнер без сети: import/OpenAPI и запрос ASGI `/health` с тестовым ключом | PASS, 10 маршрутов, HTTP 200 |
| `codex-semgrep` только по изменённым аналитическим/security путям | PASS, findings отсутствуют |
| `ai-security-check staged` с временным GIT_INDEX_FILE | PASS, утечек нет; реальный индекс не изменён |
| Playwright: вход, выбор сборок, сравнение, фильтр 500+ игр, сортировка | PASS |
| Playwright: карты, матчапы, график истории, Wild, сигналы, бюджет | PASS |
| Playwright: 1440px и 390px, отсутствие горизонтального переполнения страницы | PASS |
| Playwright: ключ отсутствует в localStorage/sessionStorage, logout очищает данные | PASS |
| Запрос без ключа | HTTP 401, private/no-store |

Docker build выполнялся командой:

```sh
tar -cf - Dockerfile requirements.txt app web scripts .env.example | docker build --tag hs-data-api:meta-intelligence-lab-check -
```

Браузер проверял локальный сервер с `--lifespan off`, отдельной временной БД,
синтетическими fixtures и одноразовым тестовым ключом. Production данные, ключи
Scrape.do и реальные социальные API не использовались. На снимках панели есть
явная пометка о тестовых данных.

## Ограничения первого выпуска

- Охват вариантов ограничен доступной выдачей источника и бюджетом страниц.
- Карточные impact-показатели относятся к архетипу; причинный эффект замены и
  общий рейтинг «сломанных карт» не выдумываются.
- Недельное окно может пересекать патч: без проверенных границ периода оно не
  может подтверждать сигнал.
- Подтверждение статистической значимости winrate/matchup требует независимых
  выборок; такие сигналы пока observing/early. Streamer-сигналы требуют доступных
  устойчивых author references; строки без provenance не заменяют доказательства.
- До включения дополнительных сборов нужен проверенный начальный расход текущего
  месяца. Critical jobs продолжают работать и могут превысить порог 700000.
- История сохраняется без автоматической очистки; архивирование с учётом ссылок
  evidence и измерение точности сигналов через 24ч/72ч/7д — следующий этап.
- Social/LLM — только интерфейсы, без вызовов и автопубликации.

## Изменённые файлы этой задачи

- `.env.example`
- `app/fetcher.py`
- `app/firecrawl_backend.py`
- `app/hsguru_archetype_analysis.py`
- `app/hsguru_meta_matrix.py`
- `app/main.py`
- `app/meta_intelligence/__init__.py`
- `app/meta_intelligence/__main__.py`
- `app/meta_intelligence/budget.py`
- `app/meta_intelligence/collectors.py`
- `app/meta_intelligence/freshness.py`
- `app/meta_intelligence/models.py`
- `app/meta_intelligence/selectors.py`
- `app/meta_intelligence/service.py`
- `app/meta_intelligence/signals.py`
- `app/meta_intelligence/snapshots.py`
- `app/meta_intelligence/social.py`
- `app/meta_intelligence/store.py`
- `app/public_cache.py`
- `app/routers/meta_intelligence.py`
- `app/scrape_do_backend.py`
- `app/storage.py`
- `docs/SCRAPE_PROVIDERS.md`
- `docs/SECURITY_AND_PARSING.md`
- `docs/specs/META_INTELLIGENCE_LAB.md`
- `scripts/export-bundle.sh`
- `scripts/import-bundle.sh`
- `systemd/hs-data-api-meta-lab-details.service`
- `systemd/hs-data-api-meta-lab-details.timer`
- `systemd/hs-data-api-meta-lab-hot.service`
- `systemd/hs-data-api-meta-lab-hot.timer`
- `tests/fixtures/meta_lab.json`
- `tests/meta_lab_fixtures.py`
- `tests/test_meta_intelligence.py`
- `tests/test_meta_intelligence_api.py`
- `tests/test_meta_intelligence_collection.py`
- `web/index.html`
- `web/meta-intelligence.css`
- `web/meta-intelligence.html`
- `web/meta-intelligence.js`
- `docs/specs/META_INTELLIGENCE_LAB_REPORT.md`


## Выпуск на текущий API

Кандидат: `/srv/projects/data/hearthstone-parses-meta-lab-release`.
Адаптер поддерживает matrix slices, отдельные даты компонентов analysis v2 и
атомарные deck catalogs. Ранг/период сборок не смешиваются; при дубликатах
выбирается более поздняя дата измерения самой сборки. Нулевое число наблюдений
сохраняет отсутствующий impact, без подстановки нуля.

Staging — изолированный контейнер `hs-meta-lab-staging` на localhost:18876,
с копией опубликованных HSGuru JSON, собственной Lab SQLite и тестовым ключом.
Production secrets в staging не передавались; lifespan отключён, платных запросов нет.
Импортировано 264 снимка; 5 неполных/невалидных срезов отклонены.

В production Compose добавлен необязательный `/etc/hs-data-api-meta-lab.env`.
Для первого выпуска он содержит только `HS_META_LAB_ENABLED=1`.
Таймер `hs-data-api-meta-lab-sync.timer` импортирует существующие публикации и
пересчитывает сигналы каждые 30 минут. Hot/details timers не активируются до
проверки начального расхода и актуального патча. Unknown patch не подтверждает
сигналы. Новая БД находится отдельно от основной и сохраняется в прежнем data volume.

Rollback: остановить и отключить `hs-data-api-meta-lab-sync.timer`, вернуть
tracked source к `d0c4fef`, назначить образ `hs-data-api:rollback-d0c4fef-meta-lab`
тегу `hs-data-api:local`, затем выполнить `docker compose up -d --no-deps api`
из `/srv/hs-data-api` и проверить `/v1/health` и host timer exporter.
Lab SQLite и данные источников при откате сохраняются. Секреты не меняются.
