# Диагностика

## Сайт не открывается

Проверьте внешний и локальный health. Если локальный API работает, проблема
обычно находится в DNS, TLS или reverse proxy. Если нет — в контейнере/service.

```bash
curl -i https://api.kolodahearthstone.com/health
curl -fsS http://127.0.0.1:18081/health | jq .
docker compose ps
```

## Белый экран панели

1. Узнайте HTTP status.
2. Проверьте Nginx и PHP‑FPM log за этот request.
3. Проверьте наличие runtime config без вывода secrets.
4. Проверьте browser console/network.
5. Запустите `make panel-check`.

Не включайте публичный PHP error display.

## API зелёный, данные старые

```bash
curl -fsS https://api.kolodahearthstone.com/sources | jq .
docker exec hs-data-api python -m app.cli freshness-check --since-hours 48
docker exec hs-data-api python -m app.cli quality-check
```

Это parser/data freshness degradation, а не liveness failure.

## Один источник не обновляется

Проверьте route, provider status, auth/challenge, lock, contract, semantic и
regression rejection. Только после этого запускайте один точечный refresh.

## GraphQL не работает

- `VALIDATION_ERROR`: query/filter/pagination;
- `UNAUTHORIZED`: credential;
- `FORBIDDEN`: missing scope;
- `SERVICE_UNAVAILABLE`: PostgreSQL.

Typed public query может работать, когда `collections`/`records` закрыты без
`database:read`.

## Изображение сломано

Проверьте status, MIME и media index. BG hero должен использовать квадратный
portrait, а не карточный render. UI обязан показать placeholder.

## После восстановления

Проверьте исходный пользовательский сценарий, `make check`, health,
freshness/quality, GraphQL и отсутствие failed services.

Полный runbook: [docs/TROUBLESHOOTING.md](../docs/TROUBLESHOOTING.md).
