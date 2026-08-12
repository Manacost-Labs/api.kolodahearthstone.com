# Диагностика и восстановление

Начинайте с симптома. Не запускайте полный refetch, пока не поняли, что именно
сломалось: он может скрыть первичную причину и потратить provider credits.

## Быстрая развилка

| Симптом | Первая проверка |
| --- | --- |
| Сайт не открывается | HTTP status панели, Nginx и PHP‑FPM |
| Белый экран после входа | PHP error log, auth session, `config.php`, browser console |
| API недоступен | `/health`, контейнер/service, reverse proxy |
| `/health` зелёный, но данных нет | `/sources`, freshness и quality checks |
| Один источник старый | Его status, effective state и последняя ошибка |
| GraphQL вернул ошибку | HTTP status + `errors[].extensions.code` |
| Токен не работает | `/v1/auth/token`, срок, отзыв и scopes |
| Картинка не отображается | HTTP/MIME URL, локальный media index, fallback placeholder |
| Статистика пустая | Фильтры, source dataset, normalizer/import status |

## 1. API не отвечает

```bash
curl -i https://api.kolodahearthstone.com/health
curl -fsS http://127.0.0.1:18081/health | jq .
docker compose ps
```

- внешний host не отвечает, локальный отвечает: проверяйте DNS/Nginx/TLS;
- локальный endpoint не отвечает: проверяйте контейнер и его последние логи;
- API отвечает 500: ищите первую ошибку запуска, не повторяющийся secondary
  traceback.

## 2. Панель показывает белый экран

Проверьте по порядку:

1. HTTP status главной страницы и redirect GitHub OAuth.
2. Nginx error log и PHP‑FPM log за тот же request window.
3. Наличие runtime `config.php` и доступность configured database без вывода
   credentials.
4. Browser console и network request, который первым вернул `4xx/5xx`.
5. Контракты панели:

```bash
make panel-check
```

Не включайте публичный вывод PHP errors на production: он может раскрыть пути и
конфигурацию.

## 3. API работает, но источник stale/LKG

Read-only диагностика:

```bash
curl -fsS https://api.kolodahearthstone.com/sources | jq .
docker exec hs-data-api python -m app.cli freshness-check --since-hours 48
docker exec hs-data-api python -m app.cli quality-check
```

С admin token:

```bash
curl -fsS https://api.kolodahearthstone.com/ops/summary \
  -H "Authorization: Bearer ${KHS_ADMIN_TOKEN}" | jq .
```

`ok_cached` означает, что API доступен и отдаёт LKG, но последний refresh не
прошёл. Найдите terminal error конкретного source и только затем выбирайте
точечный refresh.

## 4. Один parser source не обновляется

Проверьте:

- ожидаемый маршрут источника и обязательные зависимости;
- provider response category: auth, challenge, timeout, limit или validation;
- не занят ли resource lock;
- прошёл ли fetch, но был отклонён contract/semantic/regression gate;
- соответствует ли snapshot текущему patch identity;
- не закончился ли provider budget/circuit breaker.

Точечный refresh:

```bash
docker exec hs-data-api python -m app.cli refresh \
  --source SOURCE_ID \
  --require-all-ok
```

Эта команда обращается к upstream и может расходовать лимит. Не повторяйте её
циклически без анализа новой ошибки.

## 5. GraphQL error

Прочитайте `errors[].extensions.code`:

| Код | Причина |
| --- | --- |
| `VALIDATION_ERROR` | Неверный filter, field, collection или pagination |
| `UNAUTHORIZED` | Токен отсутствует/неверен/истёк/отозван |
| `FORBIDDEN` | Нет `database:read` |
| `SERVICE_UNAVAILABLE` | Центральная PostgreSQL‑база временно недоступна |

Проверка identity токена:

```bash
curl -fsS https://api.kolodahearthstone.com/v1/auth/token \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" | jq .
```

Если typed public query работает, а `collections`/`records` нет, почти всегда
нужен `database:read` или восстановление PostgreSQL.

## 6. Токен истёк или потерян

1. Выпустите новый токен с теми же минимальными scopes.
2. Обновите secret consumer.
3. Проверьте `/v1/auth/token` новым credential.
4. Отзовите старый id.

Секрет потерянного токена восстановить нельзя. Не удаляйте единственный рабочий
`tokens:manage` credential до проверки замены.

## 7. Пустая статистика в панели

Разделите проблему по слоям:

1. Есть ли source в **Обзоре**?
2. Есть ли опубликованный dataset?
3. Соответствуют ли format/rank/period/mode фильтры доступному срезу?
4. Завершился ли import в PostgreSQL/нормализатор?
5. Возвращает ли защищённый `analytics.php` нормализованные rows?
6. Есть ли JavaScript error при рендере?

Если raw dataset есть, а нормализованных строк нет, проблема находится между
importer/normalizer и panel gateway, а не в upstream parser.

## 8. Сломанное изображение

Проверьте URL без cookies/tokens:

```bash
curl -I 'https://api.kolodahearthstone.com/uploads/path/to/image.webp'
```

Ожидается успешный status и image MIME. Для BG‑героев используйте квадратный
portrait art; карточный render героя не является корректной заменой. Панель
должна показать устойчивый placeholder и посчитать broken image, а не ломать
таблицу.

## 9. PostgreSQL platform

```bash
platform/scripts/verify-platform.sh
platform/scripts/verify-data.php
systemctl status hs-data-platform-import.service
systemctl status hs-data-platform-backup.service
```

При восстановлении сначала разверните dump в отдельную пустую базу и выполните
verification. Не перезаписывайте рабочую `hs_data` до проверки count, keys и
API contracts. Полный runbook: [platform/docs/OPERATIONS.md](../platform/docs/OPERATIONS.md).

## 10. Проверка после исправления

```bash
make check
make security
curl -fsS http://127.0.0.1:18081/health | jq .
docker exec hs-data-api python -m app.cli freshness-check --since-hours 48
docker exec hs-data-api python -m app.cli quality-check
```

Исправление считается завершённым, когда восстановлен нужный пользовательский
сценарий, причина отражена в тесте/контракте, а не только исчезла строка в логе.
