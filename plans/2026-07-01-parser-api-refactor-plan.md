# План: укрепление парсера и реструктуризация API hs-data-api

> Оркестровочный план для последовательного выполнения по фазам, каждая фаза самодостаточна
> и может выполняться в новом чат-контексте. После каждой фазы — коммит в git и push в
> https://github.com/Manacost-Labs/api.kolodahearthstone.com. На каждой фазе исполнитель ОБЯЗАН запускать
> QA-сабагента параллельно с основной работой (см. раздел «QA-сабагент» в каждой фазе).
>
> Прод: контейнер `hs-data-api` (образ `hs-data-api:local`), код запечён в образ —
> деплой = `sudo docker compose -f /srv/hs-data-api/docker-compose.yml build api && sudo docker compose -f /srv/hs-data-api/docker-compose.yml up -d api`.
> Перед каждым деплоем: `sudo docker tag hs-data-api:local hs-data-api:pre-phase<N>` (откат = retag + up -d).

---

## Phase 0 — Результаты разведки (ВЫПОЛНЕНО, только читать)

### Критический факт: двусторонний дрейф репо ↔ сервер

- **GitHub `main` (4791a3bb, 2026-06-28) впереди сервера**: содержит невыкаченный рефакторинг
  валидации — `app/publish_gate.py`, `app/source_validators.py`, `tests/test_publish_gate.py`,
  `tests/test_source_validators.py`; в repo-версии `fetcher.py` валидация идёт через
  `validate_candidate_for_publish(...)`, в `scrapers/quality.py` есть `validate_structured`
  (semantic_score/semantic_issues), в статусе появилось поле `cached_dataset_age_hours`
  (уже задокументировано в `docs/API.md:156`, но на сервере этого кода НЕТ).
- **Сервер `/srv/hs-data-api` впереди репо**: `_enrich_firecrawl_bg_heroes_from_cache()` в
  fetcher.py, расширенный firecrawl-fallback список в config.py, блок `FIRECRAWL_API_KEY`/
  `HS_FIRECRAWL_*`/`HS_HSGURU_FETCH_BACKENDS` в `.env.example`, плюс 3 хотфикса от 2026-07-01:
  quality.py (пустые dual-class матчапы — норма), dataset_regression.py (arena_class_matrix
  считает только classes), refresh_log.py (orphan_status не роняет ok freshness-check).
- `app/main.py` и ВСЯ документация (.md) идентичны в обоих деревьях.
- `/srv/hs-data-api` — НЕ git-репозиторий. `gh` авторизован как Zulut30.

### Allowed APIs / фактические паттерны (проверено, НЕ предполагать другие)

| Что | Где (точно) |
|---|---|
| Образец enum | `app/source_tiers.py:9-13` — `class SourceTier(str, Enum)` |
| Канонический set ошибочных state | `app/fetcher.py:428-434`: `{"fetch_error","blocked_by_protection","http_error","quality_error","proxy_required"}` |
| Все state-строки | `ok`, `partial`, `quality_error`, `fetch_error`, `blocked_by_protection`, `http_error`, `proxy_required`; `ok_cached` — ТОЛЬКО в `status["effective_state"]` (fetcher.py:195); `never_fetched` — только default при чтении (main.py:305, cli.py:243, demo.py:118, stale_monitor.py:32, refresh_log.py:560,591); `failed`/`running` — состояния run'ов в SQLite (hsreplay_archetypes_db.py:773-777), не status-файлов |
| Маппинг state→log level | `refresh_log.py:192-199` (`_level_for`) |
| SourceContract dataclass | `source_contracts.py:7-19`; реестр CONTRACTS :25-404; API: `get_contract` :407, `regression_drop_ratio_for_source` :425, `contract_quality_report` :519, `contract_quality_ok` :565 |
| Source dataclass + реестр | `sources.py:7-22`; `SOURCES` (44 шт.) :25-334; `SOURCE_BY_ID` :337 |
| Пайплайны вне реестра | hero_details: `hsreplay_bg_hero_details.py:13` (SOURCE_ID), save_dataset :459, save_status :460-474, вход `refresh_bg_hero_details(...)` :374; archetypes_db: статусов не пишет, только SQLite (`_begin_run` :180, `_finish_run` :218), вход :689 |
| FastAPI | `APIRouter НЕ используется (0 вхождений)`; `app = FastAPI(...)` main.py:25-29; CORS :31-38; no-cache middleware :40-47; `require_admin` :54-60; применение — `dependencies=[Depends(require_admin)]` на 12 эндпоинтах (пример :92) |
| config.py | 68 голых аксессоров над os.environ (примеры :15, :27, :271-272 `stale_dataset_hours`); pydantic>=2.7 в requirements.txt, BaseSettings НЕ используется |
| Storage | `save_dataset` storage.py:118 (JSON + SQLite под `_dataset_write_lock`), `save_status` :143, `load_dataset` :147, atomic `write_json` :102; защита тестов от прод-записи :52 |
| Тесты | 31 файл `tests/test_*.py`, 28 — unittest.TestCase; фикстуры только `tests/fixtures/contracts/` (4 JSON, загрузчик test_contract_fixtures.py:17-20); conftest/pytest.ini/CI — НЕТ; pytest НЕ в requirements и НЕ в образе; Dockerfile копирует только `app/ web/ scripts/` |
| **Baseline тестов** | `cd /srv/hs-data-api && sudo docker compose run --rm -v ./tests:/app/tests api sh -c "pip install -q pytest && python -m pytest -q tests"` → **11 failed, 136 passed** (долг: напр. test_source_tiers.py:26 ждёт 40 источников, их 44) |

### Карта документации (что инвалидируется рефакторингом)

| Документ | Разделы под обновление |
|---|---|
| `README.md` | :28-126 каталог источников; :127-133 гарантии качества; :129 список state; :162+ endpoints; :36 `effective_state=ok_cached` |
| `DEPLOY.md` | :68 поимённо `validate_parsed_data` (УЖЕ устарело vs repo-код); :66,117,125,142,159-181 stale/states |
| `docs/API.md` (705 строк) | 24 endpoint-секции; :107,124 state; :156 `cached_dataset_age_hours`; :425-470 `/ops/health` |
| `docs/SECURITY_AND_PARSING.md` | §2.4 Quality gate (прямая мишень); §2.5 источники; §3.3 endpoints |
| `PROXY_AND_RELIABILITY.md` | ДУБЛЬ в корне (245 строк) и docs/ (235 строк) — консолидировать; «33 шт.» источников устарело (их 44) |
| `docs/HSREPLAY_ARCHETYPE_DATABASE.md` | endpoints `/api/db/archetypes*` |
| `.cursor/skills/hearthstone-parsing-tools/SKILL.md` | путь `/opt/hs-data-api` → факт `/srv/hs-data-api` |

### Анти-паттерны (глобально, для всех фаз)

- НЕ изобретать методы FastAPI/pydantic — только задокументированные в проекте паттерны выше.
- НЕ менять форму существующих JSON-ответов и status-файлов без явного указания фазы.
- НЕ редактировать код прямо в проде без коммита (после Phase 1 git обязателен).
- НЕ считать зелёным прогоном «11 failed» — baseline фиксирован, регресс = НОВЫЕ падения.
- Помнить: dual-class арена УДАЛЕНА ИЗ ИГРЫ НАВСЕГДА (подтверждено владельцем) — код под неё мёртвый.
- `sudo docker compose run` создаёт контейнеры — всегда `--rm`.

---

## Phase 1 — Git-ификация сервера и примирение дрейфа

**Цель:** `/srv/hs-data-api` становится git-checkout'ом репо; серверные правки и repo-рефакторинг publish-gate объединены; ничего не потеряно в обе стороны.

**Шаги:**
1. Снапшот: `sudo tar czf /home/debian/backups/hs-data-api-pre-git-$(date +%Y%m%d-%H%M).tar.gz -C /srv hs-data-api --exclude=hs-data-api/data` и `sudo docker tag hs-data-api:local hs-data-api:pre-phase1`.
2. В `/srv/hs-data-api`: `git init && git remote add origin https://github.com/Manacost-Labs/api.kolodahearthstone.com`; `git fetch origin`; создать `.gitignore` ДО add (скопировать из клона репо; убедиться что `data/`, `.env.docker`, `.env` игнорируются — проверить `git status` НЕ показывает секреты!).
3. `git checkout -b server-state && git add -A && git commit` — зафиксировать серверное состояние как есть.
4. `git merge origin/main` — конфликты ожидаются в: `app/fetcher.py` (локальный `_enrich_firecrawl_bg_heroes_from_cache` + firecrawl-fallback vs repo `validate_candidate_for_publish`), `app/scrapers/quality.py` (локальный dual-class фикс vs repo `validate_structured`), `app/config.py`, `.env.example`. Правило разрешения: **брать ОБЕ стороны** — publish-gate из репо + firecrawl-обогащения с сервера; хотфикс dual-class перенести в ту ветку валидации, которая выживет (см. Phase 6, но здесь минимально: пустые matchups ≠ ошибка).
5. `requirements-dev.txt` (новый): `pytest>=8`. В Dockerfile тесты НЕ добавлять (прод-образ чистый); канонический прогон — команда из Phase 0.
6. Прогнать тесты (baseline-команда). Ожидание: НЕ хуже 11F/136P; repo-тесты `test_publish_gate.py`, `test_source_validators.py` должны пройти.
7. Build + deploy + смоук (см. чеклист). Merge в `main`, push.

**Верификация:**
- [x] `git status` чистый; `git log origin/main..HEAD` показывает только осмысленные коммиты
- [x] `grep -c firecrawl_fallback_source_ids app/fetcher.py` == 3 (серверная сторона не потеряна)
- [x] `ls app/publish_gate.py app/source_validators.py` — существуют (repo-сторона взята)
- [x] `git show HEAD:.gitignore | grep -E "^data/|\.env"` — секреты и данные вне git; `git ls-files | grep -E "\.env$|\.env\.docker"` — ПУСТО
- [x] Тесты: 288P, 0F на последнем локальном прогоне
- [ ] После deploy: `curl -sk --resolve api.hs-manacost.ru:443:151.80.21.140 https://api.kolodahearthstone.com/health` → `"ok":true`; `/datasets` — 46 источников, 0 с ошибками; `freshness-check` (docker compose run … `python -m app.cli freshness-check --since-hours 48`) → exit 0
- [ ] Ночные таймеры (kolodahs/hs-data-api-docker-*) сработали без failed на следующий день

**Анти-паттерны:** не делать `git add -A` до .gitignore; не «выбирать одну сторону целиком» в конфликтах fetcher.py; не пушить `data/`.

**QA-сабагент (параллельно, Explore):** «Сравни `git diff server-state..HEAD -- app/` с (а) списком серверных фич Phase 0 и (б) списком repo-фич; составь таблицу „фича → сохранена/потеряна → строка". Проверь `git ls-files` на секреты. Верни таблицу и вердикт.» Редеплой запрещён, пока QA не подтвердит «ничего не потеряно».

---

## Phase 2 — Погашение тестового долга (11 падающих)

**Цель:** зелёный прогон; тесты снова отражают код.

**Шаги:**
1. Прогнать baseline, собрать список падений (после Phase 1 он мог измениться — merge принёс новые тесты).
2. Чинить ОЖИДАНИЯ там, где отстали от кода: `test_source_tiers.py:26` (40 → фактические 44 источника — считать из `SOURCES`, не хардкодить), `test_source_contracts` (3), `test_structured_schema` (2), `test_hsreplay_arena_api` (2 — учесть смерть dual-class!), `test_quality_regression` (1), `test_cached_after_failure_alert` (1 — `ok_cached` семантика по fetcher.py:186-200).
3. Добавить `tests/conftest.py` + `pytest.ini` (minимум: testpaths). Каноническую команду прогона записать в README (Phase 10 её задокументирует).
4. Commit + push.

**Верификация:** `0 failed`; `git diff` не трогает `app/` (только tests/ и конфиги пайтеста) — если тест падает из-за реального бага в `app/`, НЕ подгонять тест, а завести отметку в план Phase 3.

**Анти-паттерны:** не удалять и не skip'ать тесты ради зелени; не менять поведение кода в этой фазе.

**QA-сабагент:** «Для каждого изменённого теста: проверь, что новое ожидание соответствует ФАКТИЧЕСКОМУ поведению кода (укажи file:line подтверждения) и не является ослаблением проверки. Список ослаблений — как блокер.»

---

## Phase 3 — Быстрые фиксы (точечные баги)

**Цель:** снять дешёвые известные риски. Каждый фикс — с тестом.

**Шаги (все места проверены в Phase 0):**
1. **Мёртвый dual-class код:** удалить `normalize_dual_class_row` (`app/hsreplay_arena_api.py:168-179`) и чтение `payload.get("dual_class_data")` (:303-304); из валидации (quality.py / source_validators.py — где после merge живёт arena-проверка) убрать проверку matchups ПОЛНОСТЬЮ; `matchups` больше не входит в `arena_class_matrix` (согласовать с потребителями поля — grep по `matchups` в `app/` и в потребителях API; если поле отдаётся наружу — оставить пустой список для совместимости до Phase 8).
2. **IndexError-guard:** `app/hsreplay_extract.py:296,349,364` — `name[0].isalnum()` → предварительная проверка `if not name: continue` (точный паттерн по месту).
3. **ValueError-guard:** `app/hsreplay_arena_api.py:182-200` `normalize_arena_card_row` — обернуть числовые парсы в try/except с логом и `None`, не роняя refresh.
4. **Timing-safe auth:** `app/main.py:54-60` — `x_api_key == expected` → `secrets.compare_digest(x_api_key or "", expected)` (импорт `secrets`; поведение 503/401 сохранить).
5. **Per-source firecrawl cap:** `app/fetcher.py:714-730` — глобальный `_firecrawl_fallback_attempts` → `dict[str, int]` по source_id; сброс в `_refresh_sources_unlocked` (:1655) сохранить.
6. Тесты на 2-5 (unittest-стиль, как 28 существующих). Commit + push + build + deploy + смоук.

**Верификация:** `grep -rn "dual_class" app/` → 0 строк кода (комментарии допустимы); тесты зелёные; `curl /api/bg/heroes`, `/datasets/hsreplay_arena` — форма ответа не изменилась (кроме согласованного matchups); refresh арены проходит: `docker compose run --rm api python -m app.cli refresh --source hsreplay_arena` → quality ok.

**Анти-паттерны:** не расширять скоуп (никаких рефакторингов «заодно»); не менять форму status-файлов.

**QA-сабагент:** «Адверсариально проверь каждый из 5 фиксов: найди сценарий, где новый код ведёт себя хуже старого (пустые строки, None, юникод-имена, отсутствующий X-API-Key). Прогони тесты. Вердикт по каждому фиксу отдельно.»

---

## Phase 4 — Enum состояний источника

**Цель:** заменить голые state-строки типизированным enum без изменения wire-формата.

**Шаги:**
1. Новый `app/source_state.py`, СКОПИРОВАТЬ паттерн `source_tiers.py:9-13`:
   `class SourceState(str, Enum)`: `OK="ok"`, `PARTIAL="partial"`, `FETCH_ERROR="fetch_error"`, `HTTP_ERROR="http_error"`, `BLOCKED_BY_PROTECTION="blocked_by_protection"`, `PROXY_REQUIRED="proxy_required"`, `QUALITY_ERROR="quality_error"`, `NEVER_FETCHED="never_fetched"`. Отдельно `EFFECTIVE_OK_CACHED = "ok_cached"` (константа, не член основного enum — это НЕ state, а effective_state, fetcher.py:195). Плюс `ERROR_STATES: frozenset` = set из fetcher.py:428-434.
2. Заменить литералы по карте Phase 0 (таблица state-строк) — механически, файл за файлом: fetcher.py, refresh_log.py (`_level_for` :192-199 переводится на ERROR_STATES/WARN-набор), stale_monitor.py, main.py, cli.py, demo.py, rotator.py, hsreplay_bg_*-модули. `str(SourceState.OK)`-семантика (str-enum) сохраняет JSON как есть.
3. НЕ трогать: `failed`/`running` в SQLite-run'ах (это другой домен — отметить комментом), state-поля log_action-событий, где это статус бэкенда, а не источника (rotator.py:191 — разграничить осознанно).
4. Commit + push + build + deploy.

**Верификация:**
- [x] `grep -rnE '"(quality_error|fetch_error|blocked_by_protection|http_error|proxy_required|ok_cached)"' app/ | grep -v source_state.py | grep -v test` → пусто; закреплено `tests/test_source_state.py`
- [ ] Побайтовое сравнение: сохранить `/datasets/hsreplay_arena` и один status-файл ДО и ПОСЛЕ — `diff` только по fetched_at
- [ ] Тесты зелёные; freshness-check exit 0

**Анти-паттерны:** не переименовывать значения; не «улучшать» семантику переходов в этой фазе.

**QA-сабагент:** «grep-развёртка по всем строковым литералам состояний во всём app/ (включая f-строки и сравнения `.get("state") ==`); список пропущенных мест с file:line. Затем runtime-проверка: подними контейнер, дёрни /datasets, /ops/health — сверь значения state с до-рефакторинговым снапшотом.»

---

## Phase 5 — Единый реестр источников и cadence-freshness

**Цель:** все пайплайны в одном реестре; недельные источники не считаются протухшими на 3-й день; хак orphan_status убран.

**Шаги:**
1. Расширить `Source` (`sources.py:7-22`) полями `stale_hours: float | None = None` (None → глобальный `stale_dataset_hours()`, config.py:271) и `kind: str = "scrape"` (`"scrape" | "pipeline"`).
2. Зарегистрировать в `SOURCES`: `hsreplay_battlegrounds_hero_details` (kind="pipeline", stale_hours=192 — недельный таймер + запас; url/site/category взять из status-словаря hsreplay_bg_hero_details.py:460-474) и `hsreplay_archetypes` (kind="pipeline"; научить `_finish_run` дополнительно писать status-файл через `save_status` — по образцу hero_details :460-474).
3. `stale_monitor.find_stale_sources`: порог = `source.stale_hours or stale_dataset_hours()`.
4. **Убрать хак 2026-07-01** из `refresh_log.py` (фильтр orphan_status в freshness.ok) — теперь сирот не должно быть; ветку orphan-статусов в stale_monitor.py:78-97 оставить как чистый «cleanup»-репорт.
5. Проверить: `fetch_source`/refresh-циклы не должны пытаться СКРЕЙПИТЬ kind="pipeline" источники (у них свои таймеры) — исключить их из `_refresh_sources_unlocked` планирования по признаку kind.
6. Тест: freshness с hero_details возрастом 100ч → ok:true; возрастом 200ч → стейл. Commit + push + build + deploy.

**Верификация:** `freshness-check` → `stale_datasets: []` и exit 0 при штатном недельном возрасте hero_details; `/sources` отдаёт 46 записей; refresh --all НЕ трогает pipeline-источники (проверить по логам `refresh-events.jsonl`).

**Анти-паттерны:** не заводить второй реестр; не менять существующие 44 id.

**QA-сабагент:** «Проверь все читатели SOURCES (grep `SOURCES`, `SOURCE_BY_ID`) — какие места неявно предполагают „все источники скрейпятся"; список с file:line и оценкой, сломает ли их kind=pipeline. Прогони freshness-сценарии.»

---

## Phase 6 — Консолидация валидации (quality.py → contracts/publish gate)

**Цель:** одна точка истины для порогов. После Phase 1 в кодовой базе живёт publish-gate из репо (`app/publish_gate.py`, `app/source_validators.py`) — он становится ЕДИНСТВЕННЫМ домом per-source правил; `scrapers/quality.py` оставляет только структурные проверки страницы (`looks_like_real_page`).

**Шаги:**
1. Инвентаризация: выписать из `quality.py` все per-type пороги (bg_heroes<30, bg_trinkets<8, arena classes<8, arena_class_pages<10, meta-строки, `len(html)<2000`, hsguru 25_000/8_000 и т.д.) в таблицу «порог → источник(и) → новый дом».
2. Перенести пороги в `SourceContract` (расширить полями при необходимости — по образцу существующих :7-19) / `source_validators.py`; вызовы `validate_parsed_data` перевести на `validate_candidate_for_publish` везде (grep!).
3. Магические числа `looks_like_real_page` (quality.py:46-63) — в контракты как `min_html_bytes` per-site.
4. Каждый перенесённый порог — фикстурный тест (реальные payload'ы брать из `data/datasets/*.json` — обрезать до минимума, класть в `tests/fixtures/contracts/`, загрузчик — образец test_contract_fixtures.py:17-20).
5. Commit + push + build + deploy + refresh-смоук ВСЕХ tier'ов: `python -m app.cli refresh --tier light_api` (см. systemd unit'ы).

**Верификация:** таблица из шага 1 — все строки со статусом «перенесён+тест»; `grep -n "< [0-9]" app/scrapers/quality.py` — только структурные (не per-source) проверки; ночной refresh без новых quality_error (сверить `/ops/summary` sources_by_state с до-фазового снапшота).

**Анти-паттерны:** не выбрасывать порог «потому что мешает» — каждое ослабление фиксируется в коммит-сообщении с причиной; не держать порог в двух местах.

**QA-сабагент (два, параллельно):** (1) «Сверь таблицу переноса с фактическим кодом — для каждого старого порога найди новый дом или пометь LOST»; (2) «Возьми 5 реальных датасетов из data/datasets, прогони через старую и новую валидацию (скриптом в scratchpad), сравни вердикты — расхождения = блокер».

---

## Phase 7 — Устойчивость парсеров

**Цель:** деградация видима, дубли убраны, редизайн апстрима ловится тестом.

**Шаги:**
1. **parser_level:** в каскадах `hsreplay_extract.py` (тринкеты img→tr→a, :287-364) писать в структуру `"parser_level": "primary"|"fallback_tr"|"fallback_anchor"` и `"dropped_rows": N` (счётчик молча пропущенных, напр. карты без id :43-58). В publish-gate: level≠primary → log_action warn (не ошибка).
2. **Нормализация:** новый `app/parsing_normalize.py` — свести парсинг процентов (structured.py:21-26 vs :223-231), markdown-ссылки (battlegrounds_comps_parse.py:232-260 vs :430-443), `_looks_like_*_name` (structured.py:138-148 vs :413-424). Перевести оба места на общий модуль, УДАЛИВ дубли.
3. **Снапшот-фикстуры:** для 6 самых хрупких парсеров (hsreplay_extract тринкеты; structured.py bg_heroes «Placement Distribution» :155 и bg_trinkets :493-551; vicious_syndicate :54-60; battlegrounds_comps_parse; arena_class_pages) — сохранить обрезанные реальные входы (источник: `data/datasets/*.json`, `data/debug_firecrawl/`) в `tests/fixtures/snapshots/` + тест «парсер(снапшот) == эталонный структурированный выход».
4. Commit + push + build + deploy.

**Верификация:** тесты зелёные; у датасетов после ночного refresh есть `parser_level` (проверить `/datasets/hsreplay_battlegrounds_trinkets_lesser`); `grep -rn "_parse_percent_value\|_is_percent" app/ | grep -v parsing_normalize` → пусто.

**Анти-паттерны:** не менять выходную структуру датасетов (только ДОБАВЛЯТЬ поля); фикстуры — обрезанные (< 50 КБ каждая), не гигабайтные дампы.

**QA-сабагент:** «Мутационная проверка: возьми каждый снапшот, испорти его 3 способами (переименуй колонку, убери блок, поменяй порядок) — тест ОБЯЗАН упасть или выход обязан пометиться fallback/dropped. Молчаливый успех на испорченном входе = блокер.»

---

## Phase 8 — API v1: роутеры, конверт, кеширование

**Цель:** версионированный, консистентный публичный API; legacy-пути живут без изменений.

**Шаги:**
1. **Разведка потребителей (внутри фазы, до кода):** grep по серверу — кто зовёт api.hs-manacost.ru: `/var/www/koloda/data/www/bg.hs-manacost.ru`, `hs-arena.ru`, боты `/home/ubuntu/Deckview`, `Hearthstone news` (grep "api.hs-manacost"). Их пути — список «нельзя ломать».
2. Роутеры (`APIRouter` — впервые в проекте, стандартный FastAPI): `app/routers/constructed.py` (нынешние `/api/db/*` кроме bg), `app/routers/bg.py` (слить `/api/bg/*` + `/api/db/bg/*`), `app/routers/arena.py` (arena-датасеты как типизированные ручки), `app/routers/system.py` (`/sources`, `/datasets`, `/health`). Монтировать под `/v1/...`; **старые пути НЕ трогать** (остаются на app напрямую).
3. **Конверт** только для /v1: `{"data": ..., "meta": {"source_id", "fetched_at", "stale": bool, "count", "limit", "offset"}}`; pydantic-модели ответов минимум для: bg/heroes, bg/minions, constructed/archetypes, constructed/decks, arena/classes.
4. **Кеш:** middleware для GET /v1 и read-only legacy (`/api/*`, `/datasets`): `Cache-Control: public, max-age=300, stale-while-revalidate=600` + `ETag` от `fetched_at` датасета; исключить `/ops`, `/admin`, `/ui`, `/health`.
5. `docs/API.md` — раздел «v1» (полная документация — Phase 10). Commit + push + build + deploy.

**Верификация:**
- [x] Активные Deckview legacy-пути из списка потребителей закреплены response-contract тестами без изменения тела
- [x] `/v1/bg/heroes` отдаёт конверт; `openapi.json` содержит конкретные схемы
- [ ] Повторный GET с `If-None-Match` → 304; через Cloudflare (`curl https://api.kolodahearthstone.com/v1/bg/heroes -I` дважды) → `cf-cache-status: HIT` на втором
- [x] Тесты зелёные

**Анти-паттерны:** не редиректить legacy на v1; не выдумывать параметры pydantic (Field/ConfigDict по документации pydantic v2 — сверяться через Context7 при сомнении); не кешировать /admin//ops.

**QA-сабагент (два):** (1) «Продиффай снапшоты legacy-ответов до/после — любые изменения = блокер»; (2) «Ревью pydantic-моделей против реальных датасетов: прогони 44 датасета через модели, ошибки валидации — список с причинами».

---

## Phase 9 — Config на pydantic-settings (опциональная, низкий приоритет)

**Цель:** валидация env-переменных при старте вместо тихих дефолтов.

**Шаги:** добавить `pydantic-settings` в requirements; `app/settings.py` с `class Settings(BaseSettings)` (все HS_*-поля с типами и range-валидаторами — переносить группами по 10-15, начиная с числовых); 68 функций config.py становятся фасадами над singleton Settings (сигнатуры НЕ меняются — весь остальной код не трогается); parity-тест: для каждого аксессора значение до == после на текущем `.env.docker` (снять через `docker compose run api python -c ...`).

**Верификация:** parity-тест зелёный; контейнер стартует с текущим .env.docker; заведомо битое значение (`HS_STALE_HOURS=abc`) роняет старт с внятной ошибкой (а не тихий default).

**QA-сабагент:** «Сверь все 68 аксессоров: имя env-переменной, default, тип — таблица до/после. Любое расхождение default'а = блокер.»

---

## Phase 10 — Документация + GitHub

**Цель:** документация соответствует коду после всех фаз; всё запушено в https://github.com/Manacost-Labs/api.kolodahearthstone.com.

**Шаги (по карте документации Phase 0):**
1. `README.md`: каталог источников 44+2 pipeline (генерировать из `SOURCES` скриптом, чтобы не рассинхронизировалось: `python -c "from app.sources import SOURCES; ..."` → markdown-таблица); state-список из enum; endpoints с /v1; раздел «Тесты» с канонической командой.
2. `docs/API.md`: v1-эндпоинты (конверт, ETag, пагинация), обновить state-значения (:107,124,156), `/ops/health` (:425-470).
3. `docs/SECURITY_AND_PARSING.md`: §2.4 переписать под publish-gate/contracts (вместо quality.py), §2.5 — 46 источников, §3.3 — таблица открытых/защищённых с /v1.
4. `DEPLOY.md`: :68 — `validate_candidate_for_publish`; state-разделы; git-checkout деплой-процедура (после Phase 1 это git-репо!).
5. `PROXY_AND_RELIABILITY.md`: удалить дубль (оставить docs/-версию, в корне — ссылку); «33 шт.» → актуально.
6. `.cursor/skills/hearthstone-parsing-tools/SKILL.md`: `/opt/hs-data-api` → `/srv/hs-data-api`.
7. `docs/PARSER_IMPROVEMENT_PLAN.md`: отметить выполненное, сослаться на этот план.
8. Финальный push; проверить `gh api repos/Zulut30/hearthstone-parses --jq .pushed_at` — сегодняшняя дата.

**Верификация:** doc-vs-code sweep (см. QA); в GitHub UI README рендерится, ссылки живые.

**QA-сабагент (три, параллельно):** (1) «Каждое имя функции/файла/поля, упомянутое в README+DEPLOY, существует в коде? grep-проверка, список битых»; (2) то же для docs/API.md против openapi.json (скачать с работающего контейнера); (3) «Каждый state/поле статуса в доках существует в enum/коде».

---

## Phase 11 — Финальная верификация

1. Полный прогон тестов (0 failed), `docker compose build` с нуля (без кеша), deploy.
2. Смоук-матрица: /health, /datasets (44+, 0 проблем), /v1-выборка, legacy-выборка потребителей, /ui, /docs.
3. `python -m app.cli freshness-check --since-hours 48` → exit 0.
4. Анти-паттерн-grep'ы: голые state-литералы (Phase 4 чеклист), `os.environ.get` вне config/settings, `== expected`-сравнения токенов, `dual_class`.
5. Сутки наблюдения: все hs-data-api-docker-* таймеры отработали, `systemctl --failed` пусто (кроме известного kolodahs-sync@constructed-cards — это ДРУГОЙ проект), `/ops/summary` без новых ошибок.
6. Снять теги `hs-data-api:pre-phase*` старше последней фазы.

---

## Порядок и зависимости

```
Phase 1 (git+merge)  ← блокирует всё
  └─ Phase 2 (тесты) ← блокирует 3-9
       ├─ Phase 3 (быстрые фиксы)      — независима от 4-5
       ├─ Phase 4 (enum)               — до 5 и 6 (они пишут state)
       │    └─ Phase 5 (реестр+cadence)
       │         └─ Phase 6 (валидация)
       │              └─ Phase 7 (парсеры)
       └─ Phase 8 (API v1)             — независима от 5-7, после 3-4
            └─ Phase 9 (config)        — опциональна, в любой момент после 2
Phase 10 (доки) ← после всех кодовых
Phase 11 (верификация) ← последняя
```

Оценка: фазы 1-3 — самые рискованные (merge + прод), выполнять по одной с суточной паузой наблюдения. Фазы 4-7 — механические при наличии зелёных тестов.
