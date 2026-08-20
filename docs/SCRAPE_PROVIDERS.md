# Scrape providers

Общий protected-page каскад реализован в `app/firecrawl_backend.py` и всегда
пробует доступных провайдеров в таком порядке:

1. **Scrape.do** — основной renderer и оплаченная подписка.
2. **Firecrawl** — резерв с ротацией пула ключей.
3. **Bright Data Web Unlocker** — платный fallback, выключенный по
   умолчанию и разрешаемый только для точных source ID.
4. **Scrapfly** — финальный резерв с отдельным пулом ключей.

Политика доступна через `scrape_source()` и `scrape_source_with_options()`.
Второй вариант принимает
`skip_providers={"scrape_do", "firecrawl", "scrapfly", "brightdata"}` для
callers, которые намеренно уже использовали конкретный слой.

## Поведение каскада

Scrape.do target headers по умолчанию используют `extraHeaders=true` и имена
`Sd-*`, сохраняя browser headers самого провайдера. Точная пересылка через
`forwardHeaders=true` включается отдельно. Временные `429/5xx` получают один
ограниченный retry; account failure сразу передаёт управление Firecrawl.
Успешный HTTP-ответ с Cloudflare challenge может переключиться со standard на
Super profile, после чего при необходимости продолжается общий каскад.

Firecrawl и Scrapfly ротируют настроенные ключи в пределах своих локальных
лимитов. Переход на Bright Data возможен только после неудачи или отсутствия
Scrape.do и Firecrawl и только при выполнении всех fail-closed условий ниже.
Если Bright Data выключен, запрещён политикой или вернул ошибку, каскад
переходит к финальному Scrapfly.

Перед возвратом результата каждый провайдер проходит общую content-acceptance
проверку: нужен HTTP 2xx, отсутствие WAF/challenge-маркеров и ожидаемая для
source страница по размеру и site identity. Поэтому большой HTTP 200 от чужого
сайта, пустая заглушка или challenge не останавливают fallback. Callers с
JSON-ответом передают специализированный validator; он применяется ко всей
цепочке, а не только к Bright Data.

Route-aware preflight не считает residential proxy или локальный FlareSolverr
глобальной зависимостью для cloud-capable source. Проверка блокирует job только
тогда, когда без соответствующей зависимости у всей выбранной job нет полезного
маршрута. Неудача одного provider/source также не должна останавливать
независимые источники той же выборки.

Residential-маршрут fail-closed: сохранённый `HS_FETCH_PROXY_URL` сам по себе
его не включает. Для осознанного возврата этого платного fallback нужны
`HS_RESIDENTIAL_PROXY_ENABLED=true` и проверенный тариф; штатный production
оставляет переключатель выключенным и предпочитает Scrape.do.

Интерактивные и Legend-каталоги HSGuru сохраняют Scrape.do как первый
оплаченный маршрут. Большой плановый all-rank fan-out сначала использует
локальный FlareSolverr: production-замер показал, что успешные Scrape.do
страницы занимают 1–2 минуты и не позволяют полному Wild-каталогу уложиться в
job timeout. Если локальный solver не подтвердил страницу, all-rank сразу
возвращается к подписанному Scrape.do и затем к удалённому каскаду Firecrawl →
Bright Data → Scrapfly. Любой маршрут обязан вернуть HTTP 2xx и структурный маркер
`deck_stats_viewport`. После двух последовательных ошибок Scrape.do короткий
пятиминутный circuit пропускает заведомо неработающий маршрут для остальных
страниц текущего запуска; новый плановый запуск начинает с закрытым circuit.

Период каталога берётся из актуальной HSGuru meta matrix (`patch_*`) и входит в
disk- и memory-cache key. Snapshot без периода или от другого патча не
переиспользуется и не смешивается с новыми строками. Небольшая, но непустая и
структурно валидная выборка сразу после патча публикуется с
`sample_state=sparse_post_patch`. Пустой срез считается подтверждённым только
при штатном сообщении HSGuru `No decks available for these filters`; пустой
viewport без этого маркера означает непроверенную/сломавшуюся разметку и
завершает срез как partial/error, а не как ложный успех или 404.
All-rank каталог запрашивает до 20 архетипов на страницу и делает не более 64
точных проверок за запуск. Очередь нулевых выборок сохраняется и циклически
сдвигается, поэтому при большом post-patch списке ни один тихий архетип не
остаётся навсегда за пределами retry budget. Непроверенный хвост публикуется с
`state=partial`, передаётся наверх как degraded exit и не мешает безопасному
join уже полученных Standard/Wild строк.
Опубликованный файл отдельно хранит acquisition текущего запуска (все
наблюдаемые попытки, HTTP/error code, профиль и provider-specific billable
units) и provenance сохранённых строк из совпадающего snapshot, поэтому
backend новых строк не приписывается старым, а кредиты разных провайдеров не
складываются в одну неоднозначную величину.

## Bright Data: условия включения

Bright Data не выполнит ни одного запроса, пока одновременно не выполнены все
условия:

- `HS_BRIGHTDATA_UNLOCKER_ENABLED=true`;
- заданы `HS_BRIGHTDATA_API_KEY` и валидный Web Unlocker
  `HS_BRIGHTDATA_UNLOCKER_ZONE`;
- source ID в точности присутствует в `HS_BRIGHTDATA_SOURCE_IDS`;
- `HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT` больше `0`;
- локальный usage ledger заранее инициализирован по текущему счётчику именно
  этой zone;
- цель использует public HTTPS на порту 443, без credentials в URL и без
  разрешения имени в private/local address.

Пустой allowlist, стандартный месячный лимит `0` и отсутствующий usage ledger
намеренно блокируют платный слой. Наличие API key само по себе ничего не
включает. Репозиторий также не утверждает, что этот fallback уже активирован в
production: отдельную Web Unlocker zone, allowlist и бюджет задаёт оператор в
secret environment после canary.

### Ограничения запроса

Bright Data fallback используется только для HTML/raw HTML/markdown. Он
намеренно пропускается, если caller передал `headers` — даже пустой dict — или
запросил screenshot. Поэтому через этот слой нельзя отправлять cookies,
авторизационные/custom headers или browser storage. Карта HSReplay получает
официальные sitemap через Scrape.do без рендеринга; она не передаёт cookies и
не переключается на Bright Data. Fan-out ограничен 32 дочерними sitemap,
количество попыток и подтверждённые request credits считаются явно, а остаток
баланса провайдера не записывается в публичный map snapshot.
Map и derived index сначала полностью проходят проверку качества в памяти и
только затем публикуются. Общий resource lock не допускает чтение индекса
плановым обновлением архетипов во время замены, а systemd ограничивает map-job
35 минутами.

Endpoint Web Unlocker зафиксирован в коде как
`https://api.brightdata.com/request`: переменной для его подмены нет. Ответ
ограничен 25 MiB, редиректы API endpoint не разрешены, а из диагностического
заголовка принимаются только allowlisted поля billing/request/render. API key,
zone, полный response body и произвольные headers не записываются в state.

### Бюджет и circuit breaker

Перед платным запросом локальный state атомарно резервирует один слот под
`flock`. Счётчик сбрасывается по UTC-месяцу и хранится в
`{HS_API_DATA_DIR}/brightdata/usage.json` с правами `0600`; ключей в этом файле
нет. Если провайдер не подтвердил `billed=false`, запрос консервативно считается
billable. Просроченная reservation после аварии процесса тоже занимает один
слот и считается ошибкой circuit breaker. Период ledger строго имеет вид
`YYYY-MM`: повреждённый или будущий период блокирует запросы. При UTC-rollover
незавершённые reservations переносятся в новый период и продолжают занимать
лимит, пока не завершатся или не будут консервативно списаны как просроченные.

На Bright Data нет дополнительного application retry: одна попытка каскада —
один Web Unlocker request, после которого при ошибке доступен Scrapfly. Число
одновременно незавершённых запросов ограничено порогом circuit breaker и
уменьшается после каждой последовательной ошибки.
После cooldown разрешается ровно один half-open probe. Cloudflare challenge,
слишком короткий документ или страница без ожидаемых признаков HSReplay/HSGuru
считается ошибкой, даже если Web Unlocker вернул HTTP 2xx. В ограниченном
post-patch `early`-режиме HSGuru использует отдельный меньший raw-size floor, но
site identity и challenge-проверки остаются обязательными. HSReplay
current-patch endpoint проверяется как JSON: необходим непустой `series.data`,
поэтому HTML-заглушка не становится успешным ответом только из-за HTTP 2xx.

Этот счётчик — локальная защита, а не account-wide billing guarantee. Удаление
volume, второй deployment с отдельным data directory или запросы другой системы
не видны локальному state. Поэтому для сервиса обязательны отдельная Web
Unlocker zone и provider-side budget/alert. Если `usage.json` отсутствует, код
fail-closed и не создаёт новый нулевой баланс автоматически.

Перед первым canary возьмите из Bright Data dashboard число уже оплаченных
запросов текущего UTC-месяца для выделенной zone и один раз создайте ledger:

```bash
docker exec hs-data-api python -m app.cli brightdata-init-usage --billed-requests CURRENT_COUNT
```

Команда откажется перезаписывать существующий ledger. После потери state сначала
сверьте dashboard, затем выполните bootstrap заново с актуальным счётчиком.

## Конфигурация

### Безопасное включение ParsesUnix

Интеграция закреплена на неизменяемом релизе `ParsesUnix v0.9.1` с проверкой
SHA-256. По умолчанию она полностью выключена. Режим выбирается отдельно для
каждого source ID:

- `legacy` — существующий маршрут без дополнительного запроса;
- `shadow` — новый прямой бесплатный запрос выполняется параллельно, проходит
  triage, парсер и publish gate, но его данные никогда не сохраняются и не
  публикуются;
- `parsesunix` — только ответ с verdict `OK` передаётся существующему парсеру,
  publish gate и regression gate. Обрезанный ответ, challenge или сетевой сбой
  отклоняются до разбора данных.

Адаптер всегда начинает с бесплатного direct transport. Только verdict
`BLOCKED` или `SOFT_BLOCK` может передать URL в платный слой. Scrape.do вызывается
не более одного раза на URL и только когда одновременно заданы явный provider
allowlist, ненулевой дневной credit limit, ненулевой request limit текущего
refresh и токен. Решение, резерв и фактическая стоимость записываются в
долговечные budget/stats/breaker ledger ядра. Наличие токена само по себе ничего
не включает; неизвестная стоимость останавливает дальнейшие платные вызовы.
После этой попытки URL не возвращается в старый provider cascade, поэтому один
block не может дважды списать Scrape.do credits. Bright Data этим слоем не
поддерживается.
API-first и Firecrawl-primary маршруты сохраняют свой приоритет, а ParsesUnix
участвует только в общей HTML-ветке или её fallback.

| Переменная | Назначение / default |
| --- | --- |
| `HS_SCRAPE_DO_TOKEN` | API token основного провайдера. |
| `HS_SCRAPE_DO_TIMEOUT_SECONDS` | Wall timeout Scrape.do, `120`. |
| `HS_PARSESUNIX_ENABLED` | Главный выключатель нового transport layer; строго `true` или `false`, default `false`. |
| `HS_PARSESUNIX_SHADOW_SOURCE_IDS` | Источники, которые сравниваются с новым транспортом без публикации его результата. |
| `HS_PARSESUNIX_ACTIVE_SOURCE_IDS` | Источники, для которых новый транспорт станет основным после shadow-проверки. Не может пересекаться с shadow allowlist. |
| `HS_PARSESUNIX_ALLOWED_PROVIDERS` | Явный ordered allowlist; на первом этапе допускается только `scrape.do`. Пустое значение запрещает все платные вызовы. |
| `HS_PARSESUNIX_MAX_CONCURRENCY` | Отдельный предел параллельных sync-вызовов нового ядра, `1..8`, default `2`. |
| `HS_PARSESUNIX_TIMEOUT_SECONDS` | Общий deadline одного transport-вызова, `5..300`, default `150`. |
| `HS_PARSESUNIX_MAX_BODY_BYTES` | Максимальный размер одного ответа, `1..32 MiB`, default `8 MiB`; превышение считается неполным ответом, а не успехом. |
| `HS_PARSESUNIX_SCRAPE_DO_DAILY_CREDIT_LIMIT` | Долговечный UTC-day лимит Scrape.do credits нового ядра; default `0`, то есть платные вызовы запрещены. |
| `HS_PARSESUNIX_SCRAPE_DO_MAX_REQUESTS_PER_REFRESH` | Атомарный лимит реальных Scrape.do-вызовов одного refresh; default `0`. Вне контекста refresh вызов запрещён. |
| `HS_PARSESUNIX_SCRAPE_DO_STRATEGIES` | Разрешённые стратегии Scrape.do; default `normal` (1 credit). `render`, `super`, `super_render` требуют явного добавления. |
| `HS_HSREPLAY_JSON_CHANNELS` | Каскад HSReplay JSON; default `flaresolverr,scrape_do,curl_cffi`: бесплатный локальный solver, затем оплаченная подписка Scrape.do, затем residential `curl_cffi`. Предпочтения контракта идут первыми, затем добавляются настроенные каналы без дублей. |
| `HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS` | Атомарный лимит зарезервированных HSReplay JSON-вызовов Scrape.do на refresh, default `120`. Этого достаточно для текущего полного набора архетипов с запасом. |
| `HS_HSREPLAY_SCRAPE_DO_MAX_CREDITS` | Атомарный stop threshold HSReplay JSON на refresh, default `160`. Ошибочные и отклонённые вызовы не возвращают резерв. Фактическая цена известна только из ответа, поэтому один уже выполненный вызов может превысить threshold; следующий вызов будет заблокирован. |
| `HS_HSREPLAY_SCRAPE_DO_MAX_CONCURRENCY` | Устаревшая настройка совместимости. Физические HSReplay JSON-вызовы принудительно сериализованы (`1` in-flight), чтобы несколько ответов не превышали credit threshold одновременно. |
| `HS_FIRECRAWL_API_KEYS` | Ротируемый пул Firecrawl. |
| `HS_FIRECRAWL_KEY_ROTATION_CREDITS` | Локальный credit ceiling на ключ. |
| `HS_SCRAPFLY_API_KEYS` | Ротируемый пул Scrapfly. |
| `SCRAPFLY_API_KEY` / `HS_SCRAPFLY_API_KEY` | Legacy single-key режим. |
| `HS_SCRAPFLY_KEY_ROTATION_CREDITS` | Локальный credit ceiling на ключ. |
| `HS_BRIGHTDATA_UNLOCKER_ENABLED` | Главный выключатель, default `false`. |
| `HS_BRIGHTDATA_API_KEY` | Bearer token Web Unlocker; только secret environment. |
| `HS_BRIGHTDATA_UNLOCKER_ZONE` | Имя именно Web Unlocker zone. |
| `HS_BRIGHTDATA_SOURCE_IDS` | Точный comma-separated allowlist source ID. |
| `HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT` | Локальный потолок requests/UTC-месяц, default `0`; не заменяет provider budget. |
| `HS_BRIGHTDATA_TIMEOUT_SECONDS` | Timeout `30..300` секунд, default `180`. |
| `HS_BRIGHTDATA_CIRCUIT_FAILURE_THRESHOLD` | Последовательные ошибки до open circuit, default `3`. |
| `HS_BRIGHTDATA_CIRCUIT_COOLDOWN_SECONDS` | Cooldown open circuit, минимум `60`, default `1800`. |

Минимальный безопасный шаблон до canary:

```env
HS_BRIGHTDATA_UNLOCKER_ENABLED=false
HS_BRIGHTDATA_API_KEY=
HS_BRIGHTDATA_UNLOCKER_ZONE=
HS_BRIGHTDATA_SOURCE_IDS=
HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT=0
```

Секреты провайдеров и request URL с credentials/token-like query нельзя
коммитить или логировать. После изменения provider configuration начинайте с
одной выделенной zone, одного дешёвого source ID и малого лимита, а production
allowlist расширяйте только по результатам quality/freshness наблюдения.

Для HSReplay JSON канал `scrape_do` использует только standard non-rendered
request (`render=false`, без Bright Data). Перед вызовом проверяются HTTPS и
точный host `hsreplay.net`; только cookies этого домена передаются как
`Sd-Cookie`. CONNECT `402/407` открывает контур до следующего refresh: следующие
proxy-backed каналы пропускаются, но независимый Scrape.do продолжает каскад.
