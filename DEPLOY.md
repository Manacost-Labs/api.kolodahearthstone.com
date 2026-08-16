# Развёртывание и перенос на другой сервер

Репозиторий: **https://github.com/Manacost-Labs/api.kolodahearthstone.com**

Канонический production checkout для Docker deployment: `/srv/hs-data-api`. Старые non-Docker units в репозитории сохраняют `/opt/hs-data-api` как legacy default; Docker units и новые установки должны использовать `/srv/hs-data-api`.

Перед продакшеном:

- **[docs/API.md](docs/API.md)** — публичные/admin endpoints, source IDs, JSON-схемы.
- **[docs/GRAPHQL_API.md](docs/GRAPHQL_API.md)** — центральная PostgreSQL-база через GraphQL.
- **[docs/API_TOKENS.md](docs/API_TOKENS.md)** — выпуск и ротация scoped-токенов.
- **[docs/HSREPLAY_ARCHETYPE_DATABASE.md](docs/HSREPLAY_ARCHETYPE_DATABASE.md)** — SQLite база HSReplay архетипов, API, CLI и расписание.
- **[docs/SECURITY_AND_PARSING.md](docs/SECURITY_AND_PARSING.md)** — секреты, API, прокси, парсинг, чеклист.

## Быстрая установка с нуля

```bash
sudo git clone https://github.com/Manacost-Labs/api.kolodahearthstone.com.git /srv/hs-data-api
cd /srv/hs-data-api
sudo ./scripts/install.sh --dir /srv/hs-data-api
sudo nano /etc/hs-data-api.env   # прокси, HS_API_KEY, опционально HSReplay/Telegram
sudo systemctl restart hs-data-api hs-flaresolverr
sudo systemctl start hs-data-api-refresh.timer hs-data-api-refresh-api.timer hs-data-api-freshness-check.timer
sudo /srv/hs-data-api/scripts/server-readiness.sh --strict
sudo /srv/hs-data-api/scripts/server-readiness.sh --strict --refresh-all
```

Legacy one-line installer по умолчанию использует `/opt/hs-data-api`; для canonical Docker layout используйте явную установку выше:

```bash
curl -fsSL https://raw.githubusercontent.com/Manacost-Labs/api.kolodahearthstone.com/main/scripts/install.sh | sudo bash
```

> Для `curl | bash` сначала убедитесь, что в `main` на GitHub актуальная версия скриптов.

## Перенос с текущего сервера (с сохранением кэша)

**На старом сервере:**

```bash
cd /srv/hs-data-api
./scripts/export-bundle.sh /tmp/hs-migrate.tar.gz
scp /tmp/hs-migrate.tar.gz user@NEW_SERVER:/tmp/
```

**На новом сервере:**

```bash
sudo ./scripts/install.sh
sudo ./scripts/import-bundle.sh /tmp/hs-migrate.tar.gz
sudo systemctl restart hs-data-api
sudo systemctl start hs-data-api-refresh.timer hs-data-api-refresh-api.timer hs-data-api-freshness-check.timer
sudo ./scripts/server-readiness.sh --strict
```

Архив содержит `/etc/hs-data-api.env`, `datasets/`, `statuses/`, сессию HSReplay и индекс карт — **не публикуйте его в открытый доступ**.

## Проверка надёжности парсера

```bash
./scripts/audit.sh
sudo ./scripts/server-readiness.sh --strict
curl -s http://127.0.0.1:8000/health | jq .
source /etc/hs-data-api.env
curl -s -H "X-API-Key: ${HS_API_KEY}" http://127.0.0.1:8000/ops/health | jq .
/srv/hs-data-api/venv/bin/python -m app.cli freshness-check --since-hours 48
/srv/hs-data-api/venv/bin/python -m app.cli quality-check
```

`/health` — лёгкий публичный liveness. Подробная диагностика источников, stale/cache state и filesystem path теперь находится в admin-only `/ops/health`.

Скрипт `audit.sh` повторно прогоняет единый `validate_candidate_for_publish` по кэшу и показывает источники с расхождением статуса и качества данных. `freshness-check` возвращает non-zero, если есть stale или `cached-after-failure` источники; это отдельный сигнал, потому что refresh job может завершиться systemd-success и при этом оставить старый кэш видимым для API. `quality-check` проверяет все доступные datasets через parser validation, source contracts и quality score. Операционно отключённые источники исключаются. Полный Vicious radar LKG при подтверждённом `upstream_publication_pending` проходит повторную структурную проверку и попадает в warnings, но продолжает считаться несвежим в `freshness-check`. Scores ниже `--min-quality-score` валят команду, а диапазон до `--warn-quality-score` попадает в warning list.

## Структура на сервере

| Путь | Назначение |
|------|------------|
| `/srv/hs-data-api` | Код приложения (git clone) и Docker Compose stack |
| `/var/lib/hs-data-api` | Кэш JSON, статусы, `hsreplay-auth.json` |
| `/etc/hs-data-api.env` | Секреты и настройки (не в git) |
| `systemd/hs-data-api*.service` | API и ежедневный refresh |
| `/srv/api-kolodahearthstone/panel/current` | Активный релиз закрытой веб-панели |
| `/srv/api-kolodahearthstone/panel-data` | Изображения, кеши и состояние панели |
| `/etc/api-kolodahearthstone/panel-config.php` | Приватная конфигурация панели |

Веб-панель разворачивается отдельно от Docker API, но из того же репозитория:

```bash
sudo ./scripts/deploy-panel.sh
sudo ./scripts/deploy-platform.sh
```

Скрипт создаёт неизменяемый release и атомарно переключает symlink `current`.
Он не удаляет `panel-data` и не копирует секреты в Git.
`deploy-platform.sh` обновляет только код, SQL, тесты и systemd units платформы;
PostgreSQL credentials, connection metadata и backups остаются на месте.

## Зависимости

- Python 3.12+, venv, `requirements.txt`
- `patchright install chromium` — HSReplay cards, BG heroes, trending
- Docker + `docker compose` — FlareSolverr (HSGuru, часть HSReplay)
- Резидентный прокси (`HS_FETCH_PROXY_URL`) — обязателен при `HS_FETCH_REQUIRE_PROXY=true`

## Обновление кода без потери кэша

**С этого workspace (rsync):**

```bash
sudo ./scripts/deploy-local.sh
```

**Или через git:**

```bash
cd /srv/hs-data-api
test -z "$(git status --porcelain)"  # не затирать локальные изменения
git fetch --prune origin
git checkout main
git pull --ff-only origin main
./venv/bin/pip install -r requirements.txt
sudo ./scripts/merge-env-example.sh /etc/hs-data-api.env
sudo ./scripts/install-docker-systemd.sh
sudo systemctl enable --now hs-flaresolverr.service hs-data-api-refresh.timer hs-data-api-refresh-api.timer hs-data-api-freshness-check.timer
sudo systemctl disable --now hs-data-api-refresh-protected.timer 2>/dev/null || true
sudo systemctl restart hs-data-api hs-flaresolverr
./scripts/audit.sh
```

Перед этим merge PR и выбор release commit выполняются отдельно. Не деплойте непроверенную feature-ветку и не используйте `git reset --hard` для обновления production checkout.

Production refresh schedule:

- `hs-data-api-refresh.timer`: full parser run at `07:00 Europe/Warsaw`.
- `hs-data-api-refresh-api.timer`: API-tier parser run at `18:00 Europe/Warsaw`; skips browser-protected sources to reduce proxy traffic.
- `hs-data-api-freshness-check.timer`: audit at `07:45` and `18:45 Europe/Warsaw`.
- `hs-data-api-docker-firecrawl-hsreplay-map.timer`: weekly HSReplay crawl-map/index refresh at `02:35 Europe/Warsaw` on Mondays.
- `hs-data-api-docker-rebuild-hsreplay-index.timer`: daily derived-index rebuild at
  `19:15 Europe/Warsaw` from current cached datasets, without spending Firecrawl
  credits or waiting for the weekly URL map.
- `hs-data-api-docker-refresh-hsguru-meta-matrix.timer`: HSGuru Standard/Wild
  matrix at `00:00` and `12:00 Europe/Warsaw`; every rank includes rolling,
  active-patch and Violet Hold periods. A newly advertised HSGuru `patch_*`
  becomes active only after the complete matrix passes its publication gate.
- `hs-data-api-docker-firecrawl-streamer.timer`: HSGuru streamer decks every
  hour at `:15`. One or two rows are accepted only as a verified low-activity
  window when every row has deck, streamer and a decodable deckstring. The
  non-persistent recovery timer checks every ten minutes, but starts the same
  shared refresh only after a failed/LKG attempt is at least five minutes old.
- `hs-data-api-docker-refresh-hsguru-archetype-analysis.timer`: daily at
  `03:20 Europe/Warsaw`. An incomplete checkpoint younger than 12 hours is
  resumed at `:45` by a non-persistent recovery timer in batches of at most 20
  targets with a strict four-provider-failure budget per recovery run.
- `hs-data-api-docker-bg-compositions-screenshot.timer`: checks at
  `04:10`, `10:10`, `16:10` and `22:10 Europe/Warsaw`. It skips provider calls
  while the latest validated image is younger than 23 hours, then retries every
  six hours until a fresh capture succeeds. A capture is published only after
  MIME, image decoder, dimensions, redaction and crop checks; the last valid
  image remains available after a failed or concurrent capture. Failure status
  stores only a bounded error code; provider URLs, cookies and raw errors are
  not written to telemetry.
- The HSGuru matrix carries forward an unavailable slice and an unavailable
  Standard/Wild current catalog only from the last published snapshot for the
  same patch. The refresh remains `partial`, exposes `cached_slices` and
  `cached_current_formats`, and never replaces current-patch data with a
  different patch.
- The HSGuru deck-catalog job always attempts Standard/Wild for both `legend`
  and `all`. Successful catalogs are persisted independently; the job exits
  non-zero with a structured `errors` map when any catalog remains unavailable.
- `hs-data-api-docker-refresh-hsreplay-archetypes.timer`: HSReplay Standard archetype SQLite snapshots at `03:20 Europe/Warsaw` on Mondays and Thursdays.
- `hs-data-api-docker-refresh-bg-hero-details.timer` publishes both the detailed
  hero dataset and the compatible `hsreplay_battlegrounds_heroes` index from
  the same quality-gated HSReplay JSON response. Premium HTML failure therefore
  cannot leave the public hero index stale while fresh detail data is serving.
- `scripts/install-docker-systemd.sh`: автоматически устанавливает и включает все
  `hs-data-api-docker-*.timer`, поэтому новый pipeline timer не останется только
  в репозитории после следующего деплоя.
- Parser-control показывает primary и recovery schedules отдельно: recovery не
  подменяет обычный `nextRunAt`, а публикуется как `nextRecoveryRunAt`.
- `hs-data-api-docker-export-timer-state.timer`: раз в минуту атомарно экспортирует
  фактические `enabled/active/last/next/result` таймеров и их services в общий
  `data/parser-control-systemd.json`. Контейнер принимает snapshot только пока он
  моложе трёх минут; при остановке exporter админка возвращается к номинальному
  расписанию и не выдаёт устаревшее состояние за runtime. Это единственный
  host-side Python-модуль: он использует только стандартную библиотеку, запускается
  непривилегированным пользователем `debian` под Python 3.11+ и имеет отдельный
  CI smoke на 3.11; основной API и parser jobs остаются на Python 3.12 в Docker.
- `hs-data-api-protected-recovery.service`: conditional fallback launched by failed freshness audit; refreshes only stale/cached-after-failure `browser_protected` sources.
- `hs-data-api-refresh-protected.timer`: disabled by default; the morning full run already includes protected sources. Use `systemctl start hs-data-api-refresh-protected.service` only for manual recovery.

После деплоя проверьте новые поля ops:

```bash
source /etc/hs-data-api.env 2>/dev/null || true
curl -s http://127.0.0.1:8000/health | jq .
curl -s -H "X-API-Key: ${HS_API_KEY}" http://127.0.0.1:8000/ops/health | jq '{ok, sources, states, stale_count, cached_count}'
curl -s -H "X-API-Key: ${HS_API_KEY}" http://127.0.0.1:8000/health/premium | jq .
curl -s -H "X-API-Key: ${HS_API_KEY}" http://127.0.0.1:8000/ops/summary | jq '.freshness'
/srv/hs-data-api/venv/bin/python -m app.cli freshness-check --since-hours 48
/srv/hs-data-api/venv/bin/python -m app.cli quality-check
sudo systemctl start hs-data-api-docker-export-timer-state.service
jq '{provider, generatedAt, status, timingAvailable, units: (.units | length)}' \
  /srv/hs-data-api/data/parser-control-systemd.json
```

HSReplay archetype database smoke-test:

```bash
python -m app.cli refresh-hsreplay-archetypes --limit 1
curl -s http://127.0.0.1:8000/api/db/archetypes?limit=3 | jq .
curl -s http://127.0.0.1:8000/api/db/archetypes/856/mulligan?limit=5 | jq .
```

Preflight strict для cron включают постепенно: сначала `HS_REFRESH_PREFLIGHT_STRICT=false`, после 2 успешных `refresh --all` → `true`.

Green deploy для production: публичный `/health.ok=true`, admin `/ops/health.freshness_ok=true`, `/ops/summary.freshness.cached_after_failure_count=0`, `freshness-check` возвращает `0`, `quality-check.ok=true`.

Для нового сервера или сервера с большим количеством соседних сервисов используйте один readiness gate:

```bash
sudo /srv/hs-data-api/scripts/server-readiness.sh --strict
sudo /srv/hs-data-api/scripts/server-readiness.sh --strict --refresh-all  # перед переключением traffic/DNS
```

Скрипт проверяет env, venv, source tier registry, systemd timers, FlareSolverr, proxy, strict preflight, `/health`, `/ops/summary`, freshness и quality. Это основной smoke-test после переноса.

Точечный refresh:

```bash
./venv/bin/python -m app.cli refresh --source hsreplay_cards_legend_included_popularity
```

## Vicious Syndicate после выхода дополнения

Если radar-страницы требуют браузерную сессию, экспортируйте cookies только с
домена `vicioussyndicate.com` в формате Playwright storage state или
Cookie-Editor и импортируйте их:

```bash
cd /srv/hs-data-api
./venv/bin/python -m app.cli vicious-import-storage /secure/path/vicious-cookies.json
./venv/bin/python -m app.cli refresh --source vicious_syndicate_radars
./venv/bin/python -m app.cli refresh --source vicious_syndicate_live_beta
./venv/bin/python -m app.cli quality-check
```

Нормальный результат — `upstream_state=ready`. Live Beta сначала использует
согласованное непустое окно `lastDay`, затем `last3Days`, `lastWeek` или
`last2Weeks`; ladder и matchup всегда относятся к одному фактическому окну.
Если все окна пусты, `upstream_temporarily_empty` оставляет последний валидный
cache и не запускает бессмысленный browser fallback. Для radar
`upstream_publication_pending` означает, что API публикует последний полный
граф с явными номерами radar/report issue и автоматически заменит его после
появления нового. Панель показывает это как ожидание upstream, а не ошибку
парсера; `freshness-check` всё равно остаётся строгим. Пустые/повреждённые графы
не публикуются.

Timer `hs-data-api-docker-refresh-vicious-syndicate.timer` проверяет Vicious
каждые два часа с jitter до 10 минут. Никогда не ослабляйте структурный
quality-gate ради зелёного refresh.

## HSGuru stale/cached-after-failure recovery

Диагностика:

```bash
source /etc/hs-data-api.env
curl -s -H "X-API-Key: ${HS_API_KEY}" "http://127.0.0.1:8000/ops/summary?since_hours=48" | jq '{freshness, cached_after_failure_sources, backend_failures}'
curl -s -H "X-API-Key: ${HS_API_KEY}" "http://127.0.0.1:8000/ops/events?action_group=browser&since_hours=48" | jq '.[-20:]'
/srv/hs-data-api/venv/bin/python -m app.cli freshness-check --since-hours 48 --alert
```

Для HSGuru используется отдельный `HS_HSGURU_FETCH_BACKENDS` (default: `flaresolverr,patchright,scrapling,curl_cffi,cloudscraper`): два proxyless backend идут раньше residential fallback. При Cloudflare/403 browser rotator burn-ит sticky proxy session; timeout и quality failures открывают circuit только для конкретного source, чтобы один плохой endpoint не выключал backend для всей HSGuru пачки.

Recovery:

```bash
cd /srv/hs-data-api
sudo systemctl start hs-data-api-freshness-check.service
./venv/bin/python -m app.cli refresh --source hsguru_meta_wild_top_legend
./venv/bin/python -m app.cli refresh --tier browser_protected
```

Если `cached_after_failure_count` остаётся >0, не включайте protected timer: сначала проверьте proxy egress (`proxy-check`, `proxy-rotation-check`) и последние `browser.backend.fail`/`proxy.session.burn` события. Для разового восстановления используйте `systemctl start hs-data-api-refresh-protected.service`.
