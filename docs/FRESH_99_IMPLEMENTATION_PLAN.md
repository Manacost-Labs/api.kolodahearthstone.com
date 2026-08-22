# План достижения 99% fresh-only

## Цель и правила измерения

Цель считается достигнутой только после полного 30-дневного окна, в котором:

- не менее 99% допустимых логических обновлений завершились как
  `fresh_published`;
- не менее 99% допустимых обновлений имеют доказательство полноты;
- расписание имеет 100% временное покрытие, а повторные HTTP-попытки одного
  обновления не увеличивают знаменатель;
- LKG, provisional и HTTP 200 без публикации не считаются свежим успехом;
- независимо подтверждённое отсутствие upstream-артефакта исключается только
  из parser SLO и остаётся проблемой end-to-end freshness;
- Bright Data residential и дорогие стратегии Scrape.do не включаются
  автоматически.

Исходная контрольная точка 2026-08-22: 616 из 631 допустимого обновления за
24 часа были fresh-only (97,62%), все 15 LKG пришлись на три HSGuru matchup-
источника. При таком объёме бюджет 99% допускает не более 6 плохих исходов.
Доказательство полноты имели 539 из 631 fresh-only попытки (85,42%).

## Фаза 1. Восстановить управляемое обновление HSGuru

### Задача 1.1. Точная причина отказа ParsesUnix

**Изменение:** преобразовать детерминированные verdict ParsesUnix в bounded
`failure_reason_code` и сохранять точную причину решения о платном fallback без
URL, тела ответа и секретов.

**Критерии приёмки:**

- `BLOCKED`, `SOFT_BLOCK` и `ACCESS_DENIED` становятся `access_blocked`;
- `RATE_LIMITED`, `AUTH_REQUIRED`, `ORIGIN_DOWN` и `PARSE_FAIL` получают
  соответствующие bounded-классы;
- известный verdict больше не попадает в `unknown`;
- каждый пропущенный Scrape.do-вызов имеет одну причину: verdict, конфигурация,
  budget state, router, breaker, refresh cap или budget hold.

**Проверка:** targeted tests ParsesUnix/fetcher/reliability, затем `make check`.

**Зависимости:** нет. **Размер:** M (3-5 файлов).

### Задача 1.2. Устранить `UNKNOWN_SPEND`

**Изменение:** по данным кабинета Scrape.do сверить единственную незавершённую
reservation и выполнить штатный `ws-budget reconcile` с фактической стоимостью.
Нулевую стоимость не предполагать.

**Критерии приёмки:**

- budget state равен `OK` или `WARNING`;
- нет reservation в `UNKNOWN`;
- фактическая стоимость записана как exact;
- лимиты остаются 10 credits в UTC-день и 3 запроса на refresh.

**Проверка:** read-only budget summary до и после reconcile.

**Зависимости:** требуется фактическая стоимость из кабинета провайдера.
**Размер:** S, операционный шаг.

### Задача 1.3. Ограниченный HSGuru canary

**Изменение:** после нормализации бюджета запустить только три active HSGuru
matchup-источника, не `refresh-all`.

**Критерии приёмки:**

- все три исхода `fresh_published`, а `cached_after_failure` для них равен 0;
- проходят transport, schema, semantic, completeness и publication gates;
- каждый платный запрос и его стоимость учтены точно;
- при повторной блокировке данные остаются LKG, а причина видна как
  `access_blocked`, без перехода на residential.

**Проверка:** source status, quality-check, freshness-check и reliability funnel.

**Зависимости:** задачи 1.1 и 1.2. **Размер:** S, bounded production canary.

## Фаза 2. Восстановить control plane

### Задача 2.1. Безопасная конфигурация recovery-worker

**Изменение:** worker не должен создавать HTTP-клиент до наличия реально
заявленной transport-цепочки. В active-режиме отсутствие валидного
`HS_ORCHESTRATOR_API_KEY` должно завершать запуск bounded-статусом конфигурации,
а не traceback каждую минуту.

**Критерии приёмки:**

- пустой ключ не затрагивает ledger, если transport-цепочки нет;
- при due transport-цепочке конфигурационная ошибка не выполняет HTTP-запрос и
  не выдаёт секрет;
- worker остаётся off-by-default;
- после установки ключа три последовательных запуска завершаются без падения.

**Проверка:** unit/CLI tests и systemd canary после deployment.

**Зависимости:** нет. **Размер:** M.

### Задача 2.2. Deployment preflight

**Изменение:** перед включением worker проверять длину ключа, безопасный base URL
и совпадение readiness control-plane контракта. Секрет хранить только в
production env.

**Критерии приёмки:** worker timer нельзя активировать с неполной
конфигурацией; readiness не выводит значение ключа.

**Проверка:** shell/unit tests и `make security`.

**Зависимости:** 2.1. **Размер:** S.

## Фаза 3. Исправить логические расписания

### Задача 3.1. Идемпотентная screenshot-задача

**Изменение:** materialize schedule occurrence до claim; отсутствие occurrence
обрабатывать как явный ineligible/reconciled исход, а не падение сервиса.

**Критерии приёмки:** два последовательных шестичасовых окна без
`OccurrenceNotFoundError`; одна occurrence имеет один terminal outcome.

**Проверка:** schedule-ledger tests и два production-цикла.

**Зависимости:** нет. **Размер:** M.

### Задача 3.2. Vicious как публикационный цикл

**Изменение:** дешёвый probe проверяет наличие Radars; полный refresh становится
due только при появлении артефакта. Проверки одного report issue складываются в
одно логическое end-to-end событие.

**Критерии приёмки:** upstream delay остаётся виден, но не создаёт несколько
ошибок одного артефакта; parser SLO и end-to-end SLO не смешиваются.

**Проверка:** Vicious/reliability/schedule tests.

**Зависимости:** корректный convergence worker. **Размер:** M.

## Фаза 4. Доказать чистоту и полноту

### Задача 4.1. Completeness evidence для каждой due-попытки

**Изменение:** для каждой семьи источников сохранять ожидаемые измерения,
фактические строки, уникальность, обязательные поля, fill rate, patch/report ID и
сравнение с последней стабильной публикацией.

**Критерии приёмки:** после due-запуска ни один instrumented source не остаётся
`completeness=unknown`; rolling-24h evidence coverage не ниже 99%; неполный
кандидат не публикуется.

**Проверка:** по одной regression fixture на каждую source family и публичный
reliability report.

**Зависимости:** фазы 1-3. **Размер:** серия M-срезов, не один большой diff.

### Задача 4.2. Patch-aware baseline

**Изменение:** меньший объём после патча принимается только при валидном patch ID,
стабильной структуре и повторном согласованном наблюдении. Gate не ослабляется
ради зелёной панели.

**Критерии приёмки:** реальное уменьшение после патча может стать новым stable
baseline; пустой или частичный ответ остаётся rejected/LKG.

**Проверка:** pre-patch/post-patch regression fixtures.

**Зависимости:** 4.1. **Размер:** M.

## Фаза 5. Наблюдаемость и доказательство SLO

### Задача 5.1. Честная панель

**Изменение:** отдельно показывать availability, parser fresh-only, verified
complete+fresh и end-to-end freshness; рядом показывать числитель, знаменатель,
allowed bad и причины paid fallback.

**Критерии приёмки:** LKG не делает fresh-показатель зелёным; long-cadence source
показывается как `not due`; одна upstream-задержка не маскируется и не
дублируется.

**Проверка:** API contract и panel view tests.

**Зависимости:** 1.1, 3.2, 4.1. **Размер:** M.

### Задача 5.2. Burn-rate alerts и 30-дневный gate

**Изменение:** предупреждать при расходовании 50%, 100% и 200% суточного error
budget; не объявлять 99% до полного окна с 100% schedule coverage.

**Критерии приёмки:** алерт различает upstream, transport, budget, contract и
schedule gap; месячный статус остаётся `collecting`, пока окно неполно.

**Проверка:** deterministic report fixtures и 30 дней production observation.

**Зависимости:** все предыдущие фазы. **Размер:** M плюс наблюдение.

## Контрольные точки и откат

После каждых 2-3 задач выполняются targeted tests, `make check`, `make security`
и отдельный логический commit. Deployment начинается с одного bounded canary;
откат — предыдущий image/commit и `HS_CONVERGENCE_WORKER_MODE=off`. Изменение
publication gates, бюджета или residential-политики не совмещается с другими
срезами.

Первый checkpoint считается пройденным, когда три HSGuru-источника снова fresh,
recovery-worker не падает и в новых terminal outcomes нет `unknown` для
известных ParsesUnix verdict. Это должно убрать текущие 15 HSGuru LKG в сутки;
после этого основным ограничением станет completeness evidence, а не транспорт.
