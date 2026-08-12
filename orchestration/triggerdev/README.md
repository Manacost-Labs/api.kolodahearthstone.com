# Trigger.dev control plane

Этот пакет подготавливает Trigger.dev как внешний scheduler/control plane, но не
переносит сами парсеры за пределы Hearthstone-сервера. Trigger.dev отправляет
только идентификаторы источников/секций, а durable parser-control queue локально
выполняет refresh, применяет locks, publish gate и last-known-good policy.

Интеграция в репозитории **не означает, что production schedule уже включена**.
`hearthstone-vicious-syndicate-canary` намеренно не содержит declarative cron;
расписание добавляется только после ручного canary и handoff с systemd.

## Граница доверия

Trigger.dev имеет доступ только к двум scoped endpoints:

- `POST /admin/orchestrator/parser-runs` — поставить точную selection в локальную
  очередь;
- `GET /admin/orchestrator/parser-runs/{run_id}` — прочитать минимальный статус
  одного run.

Они принимают отдельный `X-Orchestrator-Key`, а не admin `X-API-Key`. Сервер
fail-closed отклоняет конфигурацию, если `HS_ORCHESTRATOR_API_KEY` короче 32
символов или совпадает с `HS_API_KEY`. Используйте случайный отдельный секрет не
короче 32 символов и храните его только в server/Trigger secret environment.

Provider keys, cookies, FlareSolverr, browser state и data directory никогда не
передаются в Trigger.dev. `PARSER_CONTROL_BASE_URL` обязан использовать HTTPS и
не может содержать credentials, query или fragment.

## Обязательная конфигурация

На Hearthstone-сервере:

```env
HS_ORCHESTRATOR_API_KEY=<отдельный случайный секрет не короче 32 символов>
```

В Trigger.dev environment/secrets:

```env
TRIGGER_PROJECT_REF=<project ref>
PARSER_CONTROL_BASE_URL=https://api.kolodahearthstone.com
PARSER_ORCHESTRATOR_TOKEN=<тот же scoped secret>
PARSER_RUN_TIMEOUT_SECONDS=4500
```

`PARSER_RUN_TIMEOUT_SECONDS` необязателен, принимает `60..7200` секунд и по
умолчанию равен `4500`. Не сохраняйте реальные значения токенов в этом каталоге,
`.env`, build logs или Git.

## Задания и результат

- `hearthstone-parser-run` — вручную вызываемая generic task для явных
  `sourceIds`/`sectionIds`.
- `hearthstone-vicious-syndicate-canary` — schedule-ready canary только для
  `vicious_syndicate_live_beta` и `vicious_syndicate_radars`; cron отсутствует.

Обе task используют очередь с `concurrencyLimit: 1`, ставят локальную job и
проверяют её статус каждые 30 секунд через Trigger wait. Терминальные исходы:

| Trigger result | Локальный status | Смысл |
| --- | --- | --- |
| `fresh` | `succeeded` | Все источники завершились свежим `ok`. |
| `degraded` | `partial` | Хотя бы часть selection пригодна/свежа или обслуживается из LKG; остальные результаты требуют внимания. |
| Failed run | `failed` или wall timeout | Нет приемлемого результата либо локальная job не завершилась вовремя. |

## Idempotency и transient retry

Для каждого Trigger run формируется стабильный
`requestId=trigger:<run-id>:<task-id>`. Локальный server ledger связывает его с
одним parser run: повтор с той же selection возвращает уже созданную job даже
после её завершения, а повтор с другой selection отклоняется.

HTTP-клиент повторяет до трёх раз только network errors, invalid/truncated JSON
успешного ответа и статусы `408`, `425`, `429`, `5xx`; validation/auth responses
не повторяются. Между попытками используется ограниченный backoff и
`Retry-After`. Task допускает до двух attempts, но повторная постановка остаётся
идемпотентной благодаря тому же `requestId`. Это защищает локальную очередь от
дубликатов, но не делает произвольный новый Trigger run бесплатным: новый run ID
создаёт новую parser job и может расходовать provider credits.

## Проверка пакета

Требуется Node.js 22 или новее:

```bash
npm ci
npm run check
npm test
```

Для локального подключения к Trigger.dev используйте `npm run dev`; публикация
конфигурации выполняется `npm run deploy` только после создания project ref и
загрузки секретов через штатный Trigger.dev secret environment.

## Production handoff с systemd

Для одной selection всегда должен работать только один scheduler.

1. Убедитесь, что текущая systemd job завершилась, а resource lock свободен.
2. Отключите соответствующий systemd **timer**, но оставьте service установленным
   как быстрый rollback.
3. Запустите generic task вручную и проверьте local run ID, freshness, quality и
   отсутствие `degraded`.
4. Добавьте одну canary schedule в Trigger.dev и наблюдайте минимум полный
   интервал расписания.
5. Только после canary переносите следующие timer selections по одной.

Rollback: сначала отключите Trigger.dev schedule и дождитесь завершения активной
job, затем снова включите systemd timer. Никогда не включайте оба scheduler для
одних и тех же источников одновременно.
