# Имплементационный плейбук к плану рефакторинга

> Спутник к `2026-07-01-parser-api-refactor-plan.md`. Там — «что и зачем», здесь — «как именно»:
> команды, код-скетчи before→after, карта merge-конфликтов, порядок операций.
> Все file:line — по состоянию сервера на 2026-07-01; после Phase 1 сверять по git.

Обозначения: `DC` = `sudo docker compose -f /srv/hs-data-api/docker-compose.yml`,
`TESTS` = `DC run --rm -v ./tests:/app/tests api sh -c "pip install -q pytest && python -m pytest -q tests"`.

---

## Phase 1 — Git-ификация и merge: пошаговая механика

### 1.1 Подготовка (10 мин)

```bash
sudo tar czf /home/debian/backups/hs-data-api-pre-git-$(date +%Y%m%d-%H%M).tar.gz \
  -C /srv hs-data-api --exclude=hs-data-api/data
sudo docker tag hs-data-api:local hs-data-api:pre-phase1
cd /srv/hs-data-api
git init -b server-state
git remote add origin https://github.com/Manacost-Labs/api.kolodahearthstone.com
git fetch origin
# .gitignore берём из репо ДО первого add:
git show origin/main:.gitignore > .gitignore
# добавить при отсутствии: data/, .env, .env.docker, *.pyc, __pycache__/, .pytest_cache/
git status --short | grep -E "\.env|data/" && echo "СТОП: секреты в индексе" || echo OK
git add -A && git commit -m "server state as deployed 2026-07-01"
```

### 1.2 Merge: карта конфликтов и правила разрешения

`git merge origin/main` — ожидаемые конфликтные файлы и решение ПО КАЖДОМУ:

| Файл | Конфликт | Правило |
|---|---|---|
| `app/fetcher.py` (~103 diff-строки, 3 смысловых хунка) | repo: `from .publish_gate import validate_candidate_for_publish` + `gate = validate_candidate_for_publish(...)` в 3 местах c `extra={"quality_metrics": qmetrics, "publish_gate": gate.extra}`. server: `_enrich_firecrawl_bg_heroes_from_cache()` + ветка `if source.id in firecrawl_fallback_source_ids():` | **Обе стороны.** Итоговый порядок в firecrawl-ветке: `parsed = _enrich_firecrawl_bg_heroes_from_cache(source, parsed)` (server) → `gate = validate_candidate_for_publish(source, parsed, backend="firecrawl")` (repo). Обогащение ВСЕГДА до валидации. Функция `_enrich_...` целиком с сервера, вызовы gate — целиком из репо |
| `app/scrapers/quality.py` | repo: import `validate_structured` + semantic-блок в `quality_metrics()` и в `validate_parsed_data()`. server: dual-class фикс (`if matchups and len(matchups) < 50`) | **Обе стороны.** Semantic-код из репо принять полностью; в per-type ветке `arena_class_matrix` оставить серверную форму проверки matchups (в Phase 3 она удалится совсем) |
| `app/config.py` | server: `hsreplay_battlegrounds_comps`, `hsreplay_battlegrounds_heroes` в firecrawl-fallback списке | Серверная сторона (repo просто не имеет этих строк) |
| `.env.example` | server: блок `FIRECRAWL_API_KEY` + 8×`HS_FIRECRAWL_*` + `HS_HSGURU_FETCH_BACKENDS` | Серверная сторона |
| `app/dataset_regression.py`, `app/refresh_log.py` | только серверные хотфиксы, в репо этих правок нет | Серверная сторона (обычно auto-merge, конфликта не будет) |

Новые файлы из репо приходят без конфликтов: `app/publish_gate.py` (40 строк: backend-policy
→ делегат в `validate_parsed_data`), `app/source_validators.py` (189 строк:
`ValidationIssue`/`ValidationReport` + реестр `_VALIDATORS = {"bg_heroes": _validate_bg_heroes}`
+ `validate_structured()`), `tests/test_publish_gate.py`, `tests/test_source_validators.py`.

### 1.3 Проверка результата merge (обязательный чек до build)

```bash
grep -c "firecrawl_fallback_source_ids" app/fetcher.py        # == 3
grep -c "validate_candidate_for_publish" app/fetcher.py       # == 4 (1 import + 3 вызова)
grep -n "_enrich_firecrawl_bg_heroes_from_cache" app/fetcher.py  # def + >=1 вызов
grep -n "if matchups and" app/scrapers/quality.py             # dual-class фикс жив
python3 -c "import ast; ast.parse(open('app/fetcher.py').read())"  # синтаксис
```

### 1.4 requirements-dev, тесты, деплой

```bash
printf 'pytest>=8\n' > requirements-dev.txt
$TESTS   # ожидание: НЕ хуже 11F/136P; test_publish_gate + test_source_validators — PASS
DC build api && DC up -d api
curl -sk --resolve api.hs-manacost.ru:443:151.80.21.140 https://api.kolodahearthstone.com/health
DC run --rm api python -m app.cli refresh --source hsreplay_battlegrounds_heroes  # прогон publish-gate пути
git checkout -b main --track origin/main 2>/dev/null || git branch -f main HEAD
git push origin HEAD:main
```

Если refresh heroes даёт `source semantic validation failed` — это НОВЫЙ (репозиторный)
semantic-гейт забраковал реальные данные: смотреть `semantic_issues` в статусе, при
ложном срабатывании ослабить порог в `_validate_bg_heroes` отдельным коммитом с числами в сообщении.

**Rollback:** `docker tag hs-data-api:pre-phase1 hs-data-api:local && DC up -d api`; дерево — из tar-бэкапа.

---

## Phase 2 — Тестовый долг: конкретика по каждому из 11

Порядок: сначала прогон после merge (список мог измениться), затем по группам.

| Тест | Диагноз | Правка |
|---|---|---|
| `test_source_tiers.py:26` (2 шт.) | хардкод «40 источников», их 44 (после Phase 5 станет 46) | заменить на `len(SOURCES)` / считать группы динамически из реестра; НЕ хардкодить новое число |
| `test_source_contracts` (3 шт.) | контракты добавлялись без обновления тестов | сверить каждый assert с фактическим `CONTRACTS`; ожидания — из кода, с комментарием-ссылкой на источник правды |
| `test_structured_schema` (2 шт.) | схема structured эволюционировала | прогнать реальный датасет из `data/datasets/` через парсер, обновить эталон |
| `test_hsreplay_arena_api` (2 шт.) | ожидают dual_class поведение | переписать под «matchups могут отсутствовать»; после Phase 3 — под их полное отсутствие (пометить TODO-Phase3) |
| `test_quality_regression` (1) | volatility-правило hsguru rank slice | сверить с `regression_drop_ratio_for_source` (:425, `max(default, contract)`) — тест видимо ждёт старую min-семантику |
| `test_cached_after_failure_alert` (1) | `ok_cached` семантика | эталон — fetcher.py:186-200: state="ok", effective_state="ok_cached", serving_cached_dataset=True |

`tests/conftest.py` (новый): `import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))` — чтобы `python -m pytest tests` работал и без установки пакета. `pytest.ini`: `[pytest] testpaths = tests`.

Правило: если тест ловит РЕАЛЬНЫЙ баг кода — тест не трогать, баг в список Phase 3, тест временно `@unittest.expectedFailure` с комментом `# BUG: см. план Phase 3.<n>`.

---

## Phase 3 — Быстрые фиксы: код before→after

### 3.1 Смерть dual-class

- Удалить `normalize_dual_class_row` (hsreplay_arena_api.py:168-179).
- `fetch_class_stats` (:296-317): убрать `matchups = [...]`; в возврате `"matchups": []` ОСТАВИТЬ
  (потребители могут читать ключ; убрать поле — только после аудита потребителей в Phase 8.1).
- quality.py ветка `arena_class_matrix`: удалить проверку matchups целиком:
  ```python
  if len(classes) < 8:
      return False, f"arena class stats too few ({len(classes)})"
  return True, "ok"
  ```
- dataset_regression.py:47-50 — оставить как есть (уже считает только classes).
- Тест: `fetch_class_stats`-парсинг payload'а БЕЗ ключа dual_class_data → ok.

### 3.2 Guards

hsreplay_extract.py (три места :296,:349,:364), паттерн:
```python
# before:  if name and name[0].isalnum():
# after:
if name and name[:1].isalnum():
```
(`[:1]` не кидает IndexError на пустой строке; поведение для непустых идентично).

normalize_arena_card_row (hsreplay_arena_api.py:182-200): каждый float(...) →
```python
def _safe_float(value):
    try:
        return float(str(value).replace(",", ".").rstrip("%"))
    except (TypeError, ValueError):
        return None
```
(положить рядом в модуле; в Phase 7 переедет в parsing_normalize).

### 3.3 Timing-safe auth (main.py:54-60)

```python
import secrets
def require_admin(x_api_key: Annotated[str | None, Header()] = None) -> None:
    expected = api_key()
    if not expected:
        raise HTTPException(status_code=503, detail="Admin API key is not configured")
    if x_api_key and secrets.compare_digest(x_api_key, expected):
        return
    raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")
```

### 3.4 Per-source firecrawl cap (fetcher.py:69, 714-730, 1655)

```python
# before: _firecrawl_fallback_attempts = 0            (int, глобальный)
# after:  _firecrawl_fallback_attempts: dict[str, int] = {}
# в _try_firecrawl_html / cap-проверке:
attempts = _firecrawl_fallback_attempts.get(source.id, 0)
if attempts >= firecrawl_fallback_max_attempts():   # имя аксессора сверить в config.py
    ...skip...
_firecrawl_fallback_attempts[source.id] = attempts + 1
# в _refresh_sources_unlocked (:1655): .clear() вместо = 0
```
Тест: два источника, у первого исчерпан cap → второй всё ещё получает fallback.

---

## Phase 4 — SourceState enum: механика замены

### 4.1 Новый app/source_state.py (копия паттерна source_tiers.py:9-13)

```python
from __future__ import annotations
from enum import Enum

class SourceState(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    FETCH_ERROR = "fetch_error"
    HTTP_ERROR = "http_error"
    BLOCKED_BY_PROTECTION = "blocked_by_protection"
    PROXY_REQUIRED = "proxy_required"
    QUALITY_ERROR = "quality_error"
    NEVER_FETCHED = "never_fetched"

EFFECTIVE_OK_CACHED = "ok_cached"   # значение effective_state, НЕ member основного enum

ERROR_STATES: frozenset[str] = frozenset({
    SourceState.FETCH_ERROR, SourceState.BLOCKED_BY_PROTECTION,
    SourceState.HTTP_ERROR, SourceState.QUALITY_ERROR, SourceState.PROXY_REQUIRED,
})
WARN_STATES: frozenset[str] = frozenset({SourceState.PARTIAL, SourceState.QUALITY_ERROR})
```

### 4.2 Порядок замены (по одному файлу = по одному коммиту)

1. fetcher.py: set :428-434 → `ERROR_STATES`; все `state="..."` → `state=SourceState.X`.
   str-enum сериализуется в JSON как значение — wire-формат не меняется (проверить именно
   json.dumps: `json.dumps({"s": SourceState.OK})` → `{"s": "ok"}` — pydantic/json это делают,
   но `str(SourceState.OK)` в f-строках даёт "SourceState.OK" в Py<3.11 семантике StrEnum —
   в f-строках использовать `.value`. ВАЖНО: пройти grep по f-строкам со state).
2. refresh_log.py `_level_for` :192-199 → ERROR_STATES/WARN_STATES.
3. stale_monitor.py, main.py, cli.py, demo.py — сравнения `== "ok"` → `== SourceState.OK`
   (str-enum равен строке — обратная совместимость чтения старых JSON гарантирована).
4. rotator.py:191 и лог-события: это состояния БЭКЕНДА — не трогать, пометить комментом.
5. hsreplay_bg_*-модули: `"ok"/"partial"` → enum; `"failed"/"running"` SQLite-run'ов не трогать.

### 4.3 Дифф-проверка wire-формата

```bash
curl -s localhost:18081/datasets/hsreplay_arena > /tmp/after.json   # изнутри сервера через DC exec
sudo docker exec hs-data-api python - <<'EOF'
from app.storage import load_status; import json
print(json.dumps(load_status("hsreplay_arena"), sort_keys=True))
EOF
# сравнить с до-фазовым снапшотом: поля и значения идентичны
```

---

## Phase 5 — Реестр + cadence: точки врезки

1. `sources.py:7-22` — добавить в dataclass:
   ```python
   stale_hours: float | None = None   # None => глобальный HS_STALE_HOURS
   kind: str = "scrape"               # "scrape" | "pipeline"
   ```
   (frozen dataclass, поля с default — в конец, позиционные вызовы 44 источников не ломаются).
2. В `SOURCES` добавить 2 записи (в конец, keyword-аргументами):
   `Source(id="hsreplay_battlegrounds_hero_details", url="https://hsreplay.net/battlegrounds/heroes/", site="hsreplay", category="battlegrounds", kind="pipeline", stale_hours=192, description="Weekly hero detail cache (systemd timer)")`
   и аналогично `hsreplay_archetypes` (stale_hours=30 — обновляется чаще, сверить с таймером
   `hs-data-api-docker-refresh-hsreplay-archetypes.timer`: OnCalendar → фактический период × 2 + запас).
3. `hsreplay_archetypes_db._finish_run` (:218) — добавить запись status-файла по образцу
   hero_details :460-474 (`save_status(SOURCE, {...state, fetched_at, detail...})`).
4. `stale_monitor.find_stale_sources` :25 — `limit_h = source.stale_hours or stale_dataset_hours()`
   внутри цикла (перенести из прелюдии).
5. `refresh_log.py` — УБРАТЬ хотфикс-фильтр orphan_status из freshness.ok (вернуть
   `"ok": not stale_sources and not cached_now`), т.к. сирот больше нет.
6. Планировщик: в `_refresh_sources_unlocked` (fetcher.py:1537+) исключить `kind == "pipeline"`
   из очереди скрейпа; в `/admin/refresh` и cli `refresh --source` — явная ошибка
   `"pipeline source, use its dedicated command"`.
7. Тест (freeze-time не нужен): собрать status-словарь с fetched_at=now-100h → find_stale_sources
   с stale_hours=192 не включает его; с now-200h — включает.

Проверка «читателей SOURCES» перед коммитом: `grep -rn "for source in SOURCES\|SOURCE_BY_ID" app/ | grep -v test` — каждое место аудировать на kind-пригодность (QA-сабагент фазы делает то же независимо).

---

## Phase 6 — Консолидация валидации: механика переноса

Дом для правил после merge: **`source_validators._VALIDATORS`** (per-type semantic) +
**`SourceContract`** (декларативные пороги). Сейчас в реестре один валидатор (`bg_heroes`) —
это образец для остальных.

1. Инвентаризация (скриптом, не руками): выгрепать из quality.py все числовые сравнения
   → таблица в `plans/phase6-thresholds.md`: `порог | тип | источник(и) | новый дом | статус`.
2. Для каждого structured-type из quality.py (`bg_card_stats`, `bg_trinkets`,
   `arena_winning_decks`, `arena_class_matrix`, `arena_class_pages`, `bg_heroes` (уже есть),
   `bg_comps`, `meta`, `streamer_decks`, ...) — написать `_validate_<type>` по образцу
   `_validate_bg_heroes` (source_validators.py:66-175): собрал строки → посчитал валидные →
   `report.add_issue(code=..., severity="error"/"warning")` → `report.score`.
   Числа порогов — 1:1 из quality.py (НЕ «улучшать» попутно).
3. `validate_parsed_data` (quality.py) худеет до: структурные проверки страницы
   (`looks_like_real_page`) + вызов `contract_quality_ok` + вызов `validate_structured`.
   Per-type if-лестница (:125-373) удаляется по мере переноса — по одному type за коммит.
4. `looks_like_real_page` магия (:46-63): `min_html_bytes` в SourceContract
   (default 2000; hsguru meta 25_000; hsguru matchups 8_000), поле добавить в dataclass :7-19.
5. Фикстуры: для каждого перенесённого type — обрезанный реальный `structured` из
   `data/datasets/<id>.json` (jq-вырезка первых 3-5 записей) в `tests/fixtures/contracts/`,
   тест: валидатор(фикстура) → ok; валидатор(фикстура без критического поля) → issue с нужным code.
6. Сквозной паритет-скрипт (запускать после КАЖДОГО перенесённого type):
   ```bash
   sudo docker exec hs-data-api python - <<'EOF'
   # для всех datasets: old_verdict = старая if-ветка, new_verdict = validate_structured
   # печатать расхождения; пустой вывод = паритет
   EOF
   ```

---

## Phase 7 — Парсеры: точки врезки

1. **parser_level** (hsreplay_extract.py каскад :287-364): каждый уровень возвращает
   `(rows, level_name)`; агрегатор пишет в structured `"parser_level"` и `"dropped_rows"`.
   В `_validate_*` (Phase 6) — `severity="warning"` issue при level != primary.
2. **app/parsing_normalize.py** — свести ТРИ копии: structured.py:21-26/:223-231 (проценты),
   source_validators.py:42-63 (`_parse_percent`/`_parse_decimal`/`_valid_name` — самая свежая
   и аккуратная реализация, взять её КАК ЕСТЬ за основу), battlegrounds_comps_parse.py:232-260/
   :430-443 (markdown-ссылки), structured.py:138-148/:413-424 (`_looks_like_*_name`).
   Порядок: создать модуль → переключить source_validators → structured → comps_parse →
   удалить локальные копии. Один коммит на модуль-потребитель.
3. **Снапшоты**: источник сырья — `data/datasets/*.json` (поле raw/tables) и
   `data/debug_firecrawl/`. Обрезать до <50КБ (первые N строк таблиц). Тест-матрица:
   `parse(snapshot) == golden.json` (golden генерится один раз текущим парсером и ревьюится глазами).
   Мутационный чек (QA фазы): переименование колонки/удаление блока → тест падает ИЛИ
   выход имеет parser_level=fallback / dropped_rows>0.

---

## Phase 8 — API v1: скелет

### 8.1 Аудит потребителей (до кода)

```bash
grep -rn "api.hs-manacost" /var/www/koloda/data/www/bg.hs-manacost.ru/app \
  /var/www/koloda/data/www/hs-arena.ru/app /home/ubuntu/Deckview \
  "/home/ubuntu/Hearthstone news" 2>/dev/null | grep -oP "api\.hs-manacost\.ru\S+" | sort -u
```
Результат — таблица «путь → потребитель» в plans/phase8-consumers.md. Эти пути замораживаются.

### 8.2 Роутеры (новая для проекта, стандартная для FastAPI механика)

```
app/routers/__init__.py
app/routers/deps.py        # общее: конверт, ETag-helper, require_admin реэкспорт
app/routers/bg.py          # /v1/bg/*        ← main.py /api/bg/* + /api/db/bg/*
app/routers/constructed.py # /v1/constructed/* ← main.py /api/db/* (без bg)
app/routers/arena.py       # /v1/arena/*
app/routers/system.py      # /v1/sources, /v1/datasets/{id}, /v1/health
```
Хендлеры v1 — тонкие: вызывают ТЕ ЖЕ функции данных, что legacy (вынести общую логику из
main.py-хендлеров в функции, если она inline), оборачивают в конверт:
```python
class Meta(BaseModel):
    source_id: str | None = None
    fetched_at: str | None = None
    stale: bool = False
    count: int | None = None
    limit: int | None = None
    offset: int | None = None

class Envelope(BaseModel, Generic[T]):   # pydantic v2 generic
    data: T
    meta: Meta
```
`main.py`: `app.include_router(bg.router, prefix="/v1")` и т.д. Legacy-декораторы не трогаются.

### 8.3 ETag/Cache middleware

```python
@app.middleware("http")
async def cache_headers(request, call_next):
    response = await call_next(request)
    p = request.url.path
    if request.method == "GET" and (p.startswith("/v1/") or p.startswith("/api/") or p.startswith("/datasets")):
        response.headers.setdefault("Cache-Control", "public, max-age=300, stale-while-revalidate=600")
    return response
```
ETag — на уровне хендлеров, у которых есть fetched_at (`etag = f'W/"{source_id}-{fetched_at}"'`;
сравнение с `If-None-Match` → `Response(status_code=304)`). Начать с 5 самых горячих ручек
(по access.log: `sudo awk '{print $7}' /var/www/httpd-logs/api.hs-manacost.ru.access.log | sort | uniq -c | sort -rn | head`).

### 8.4 Верификация кеша через Cloudflare

```bash
curl -sI https://api.kolodahearthstone.com/v1/bg/heroes | grep -iE "cf-cache-status|cache-control|etag"
# второй вызов: cf-cache-status: HIT
```
(CF кеширует JSON только при явных cache-заголовках — ровно то, что добавляем.)

---

## Phase 9 — Settings: паритет-механика

1. `app/settings.py`: `class Settings(BaseSettings)` c `model_config = SettingsConfigDict(env_prefix="HS_")` — ВНИМАНИЕ: не все переменные с префиксом HS_ (есть FIRECRAWL_API_KEY) — такие поля объявлять с `alias`.
2. Переносить группами: (1) числовые тайминги/пороги, (2) списки source_ids, (3) строки/пути.
3. Паритет-тест ДО переключения потребителей: снять эталон
   `DC run --rm api python -c "import app.config as c; import json; print(json.dumps({n: repr(getattr(c,n)()) for n in [...68 имён...]}))"`,
   после фасадизации — тот же вывод.

---

## Phase 10 — Доки: генерация вместо ручного письма

1. Каталог источников в README генерировать: `scripts/gen_source_catalog.py` (новый) —
   из `SOURCES` + `CONTRACTS` → markdown-таблица между маркерами
   `<!-- BEGIN AUTOGEN SOURCES -->…<!-- END -->`; запуск руками, результат коммитится.
   Тест `test_docs_sync.py`: сгенерированный блок == блоку в README (доки не разъезжаются).
2. docs/API.md v1-раздел: за основу — фактический `openapi.json` работающего контейнера
   (после Phase 8 он типизирован): `curl localhost:18081/openapi.json | jq` → ручная
   редактура описаний. Проверка QA-сабагентом: каждый path из API.md есть в openapi.json и наоборот.
3. PROXY_AND_RELIABILITY: `git rm PROXY_AND_RELIABILITY.md`, в корне README добавить ссылку
   на docs/-версию; в docs/-версии «33 шт.» → генерённое число.

---

## Сквозные правила исполнения

1. **Одна фаза = одна ветка** `phase-N-<slug>` → merge в main после зелёного QA. Push после каждой фазы.
2. **Коммиты атомарные** (один смысловой шаг), сообщения с номером фазы: `phase-4: replace state literals in fetcher.py`.
3. **Перед каждым деплоем**: `docker tag hs-data-api:local hs-data-api:pre-phaseN`.
4. **После каждого деплоя**: /health + `refresh --source hsreplay_arena` смоук + на следующее утро `systemctl --failed` и `/ops/summary`.
5. **QA-сабагенты** (из основного плана) запускаются ПАРАЛЛЕЛЬНО работе фазы, их вердикт — гейт на merge ветки.
6. Тесты: `$TESTS` перед каждым коммитом в app/.
