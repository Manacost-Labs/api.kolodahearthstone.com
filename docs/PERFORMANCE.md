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
