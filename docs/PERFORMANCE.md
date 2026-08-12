# API performance baseline

`scripts/benchmark_api.py` измеряет latency и размер ответов публичных GET
endpoints. Отчёт содержит min, p50, p95, p99, max, HTTP statuses и средний
размер body для каждого сценария.

Локальный smoke benchmark:

```bash
make benchmark-smoke
```

Произвольный локальный сценарий:

```bash
.venv/bin/python scripts/benchmark_api.py \
  --base-url http://127.0.0.1:8000 \
  --path '/v1/battlegrounds/heroes?limit=50' \
  --requests 100 \
  --concurrency 10
```

Remote target требует явного `--allow-remote`. Для него жёстко ограничены
максимум 100 запросов на сценарий и concurrency 20. Полный load/soak test
production этим инструментом запрещён; для высокой нагрузки используется
отдельное staging-окружение с копией данных.

Начальный бюджет до накопления production-истории:

| Класс запроса | p95 |
| --- | ---: |
| Cached public GET | до 100 ms |
| Обычный REST GET | до 200 ms |
| GraphQL экран приложения | до 300 ms |

Числа baseline сохраняются как CI artifact или operational report, а не
коммитятся в документацию: измерение зависит от hardware, объёма базы и
состояния кэша.

## Runtime budgets и Redis

Публичные GraphQL query кэшируются на 60 секунд: маленький LRU‑слой остаётся в
процессе API, а Redis делит ответы, Persisted Queries и rate counters между
workers. При недоступности Redis API продолжает работать с локальным bounded
fallback; durable месячные квоты токенов остаются обязательными.

GraphQL до обращения к PostgreSQL проверяет depth, aliases, число токенов и
weighted cost. По умолчанию максимальная стоимость — 5 000, body запроса —
64 KiB, body ответа — 2 MiB, timeout — 8 секунд. Значения задаются переменными
`HS_GRAPHQL_*` из `.env.example` и должны изменяться только вместе с benchmark и
наблюдением p95/p99.
