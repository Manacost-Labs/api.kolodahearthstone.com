# Эксплуатация

## Health model

Не используйте один сигнал для всей системы:

| Проверка | На какой вопрос отвечает |
| --- | --- |
| `GET /health` | API process доступен? |
| `/sources` | Какие datasets есть и когда обновлены? |
| `freshness-check` | Есть stale или LKG после последней ошибки? |
| `quality-check` | Cached datasets проходят текущие contracts? |
| `/ops/summary` | Что происходит с jobs, freshness и reliability? |
| Platform verify | PostgreSQL, panel и imports согласованы? |

## Стандартная проверка

```bash
curl -fsS http://127.0.0.1:18081/health | jq .
docker exec hs-data-api python -m app.cli freshness-check --since-hours 48
docker exec hs-data-api python -m app.cli quality-check
! systemctl --failed --no-pager --no-legend | grep -q hs-data-api
```

Platform:

```bash
platform/scripts/verify-platform.sh
platform/scripts/verify-data.php
systemctl list-timers 'hs-data-platform-*'
```

## Точечный refresh

```bash
docker exec hs-data-api python -m app.cli refresh \
  --source SOURCE_ID \
  --require-all-ok
```

Refresh обращается к upstream и может расходовать provider credits. Сначала
проанализируйте status/trace, затем выполняйте один целевой запуск.

## Deployment

Код меняется в source repo, проходит `make check` и review. Production
обновляется штатными scripts, а не ручным редактированием runtime/release.

Отдельные слои:

```bash
sudo ./scripts/deploy-panel.sh
sudo ./scripts/deploy-platform.sh
```

Panel deployment создаёт immutable release и атомарно переключает `current`, не
удаляя persistent media/config/state.

## Backups и restore

- PostgreSQL backup выполняется ежедневно;
- проверенные backups имеют ограниченную retention policy;
- restore сначала выполняется в отдельную пустую базу;
- до переключения consumers проверяются counts, keys, JSONB types и API;
- runtime bundle может содержать секреты и не должен публиковаться.

## После изменения

```bash
make check
make security
curl -fsS http://127.0.0.1:18081/health | jq .
```

Затем проверьте panel login, один public dataset, один GraphQL query,
freshness/quality и отсутствие failed units.

Полный deployment: [DEPLOY.md](../DEPLOY.md). Диагностика:
[Troubleshooting](Troubleshooting.md).
