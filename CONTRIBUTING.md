# Вклад в проект

Спасибо за улучшение Koloda Hearthstone Data Platform. Проект объединяет API,
парсеры, PostgreSQL и веб‑панель, поэтому даже локальное изменение нужно
проверять на совместимость соседних слоёв.

## Рабочий процесс

1. Создайте ветку от актуального `main`.
2. Перед изменениями выполните `git status --short` и не затрагивайте чужой diff.
3. Меняйте source‑репозиторий, а не production release/runtime copy.
4. Делайте минимальный связный diff и добавляйте тест изменённого поведения.
5. Запустите `make check` и проанализируйте `make security`.
6. Обновите документацию, если меняется публичный API, UI, data contract,
   deployment или operator workflow.
7. Откройте PR с объяснением «что», «зачем», пользовательского влияния и
   выполненных проверок.

## Настройка

```bash
make setup
make check
```

Основное приложение требует Python 3.12. Проверки панели также используют PHP,
Node.js и shell; GitHub workflow syntax проверяется `actionlint`.

## Команды

| Команда | Назначение |
| --- | --- |
| `make test` | Python tests |
| `make panel-check` | PHP/JS/Shell contracts веб‑панели |
| `make platform-check` | Миграции, normalizer и platform contracts |
| `make provider-check` | Быстрые regression tests provider cascade |
| `make check` | Полный обязательный gate |
| `make lint-report` | Текущий Ruff baseline |
| `make security` | Security baseline |

Не отключайте failing test или lint rule ради зелёного результата. Если ошибка
относится к существующему baseline и не связана с diff, зафиксируйте это в PR.

## Правила по областям

### API и парсеры

- routers находятся в `app/routers/`, scraping‑логика — не в HTTP‑слое;
- не выполняйте реальные premium/provider запросы в тестах;
- внешнюю сеть и время заменяйте fixtures/mocks;
- изменение source contract, SQLite schema или publication gate требует
  regression test;
- новый source добавляется в `app.sources.SOURCES`, после чего обновляется
  генерируемый каталог.

### Веб‑панель

- сохраняйте URL‑backed filters и keyboard accessibility;
- не передавайте внутренние API credentials в browser;
- используйте существующие table, drawer, lightbox и design tokens;
- при изменении видимого CSS обновите asset version;
- подробные UI contracts: [panel/docs/ADMIN_UI.md](panel/docs/ADMIN_UI.md).

### PostgreSQL platform

- migrations должны быть повторяемыми и проверяемыми;
- shadow sync остаётся однонаправленным до явного cutover;
- public response shapes не ломаются из-за смены storage;
- secrets/backups/runtime metadata не входят в Git.

## Документация

README — короткая карта проекта. Подробности размещайте в `docs/`, а готовые
Wiki‑страницы — в `wiki/`.

Если меняется:

| Изменение | Обновить |
| --- | --- |
| REST endpoint/ответ | `docs/API.md`, примеры и integration guide |
| GraphQL schema/limits | `docs/GRAPHQL_API.md` |
| Scope/token behavior | `docs/API_TOKENS.md` |
| Источник | `app/sources.py`, затем сгенерировать `docs/SOURCES.md` |
| UI workflow | `docs/WEB_PANEL.md`, `panel/docs/ADMIN_UI.md` |
| Deployment/таймер | `DEPLOY.md` и troubleshooting/runbook |
| Архитектурное решение | новый ADR в `platform/docs/decisions/` |

Не вставляйте реальные токены, cookies, private headers, production payloads и
дампы. Команды должны использовать placeholder environment variables.

## PR checklist

- [ ] Diff относится только к заявленной задаче.
- [ ] Добавлен/обновлён тест или contract check.
- [ ] `make check` проходит.
- [ ] Security output проанализирован.
- [ ] Публичная документация обновлена.
- [ ] Нет секретов и runtime data.
- [ ] Deployment/rollback описаны, если изменение затрагивает production.
