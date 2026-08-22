# Документация Koloda Hearthstone Data Platform

Это единая точка входа в документацию API, веб‑панели, центральной базы и
парсеров. Если вы впервые открыли проект, начните с
[быстрого старта](GETTING_STARTED.md).

## Выберите свою задачу

| Я хочу… | Откройте |
| --- | --- |
| Посмотреть данные в браузере | [Руководство по веб‑панели](WEB_PANEL.md) |
| Подключить сайт, бота или другой сервис | [Руководство по интеграции](INTEGRATION_GUIDE.md) |
| Найти подходящий набор и понять его поля | [Каталог данных](DATA_CATALOG.md) |
| Выполнить REST‑запрос | [REST API](API.md) |
| Запросить центральную базу через GraphQL | [GraphQL API](GRAPHQL_API.md) |
| Подключить TypeScript или C# SDK | [Official SDKs](SDK.md) |
| Выпустить или отозвать токен | [API‑токены](API_TOKENS.md) |
| Понять устройство системы | [Архитектура](ARCHITECTURE.md) |
| Разобраться, как проверяются данные | [Pipeline парсинга](PARSER_PIPELINE.md) |
| Развернуть или обновить production | [Deployment](../DEPLOY.md) |
| Найти причину сбоя | [Диагностика](TROUBLESHOOTING.md) |
| Измерить latency и размер API‑ответов | [Performance baseline](PERFORMANCE.md) |
| Внести изменение в репозиторий | [Contributing](../CONTRIBUTING.md) |

## Для пользователя веб‑панели

| Документ | Содержание |
| --- | --- |
| [GETTING_STARTED.md](GETTING_STARTED.md) | Первый вход, первые REST/GraphQL запросы и безопасный выпуск токена |
| [WEB_PANEL.md](WEB_PANEL.md) | Навигация, поиск, фильтры, таблицы, подробные карточки и API‑токены |
| [DATA_CATALOG.md](DATA_CATALOG.md) | Constructed, Battlegrounds, Arena, meta и готовые примеры |
| [SOURCES.md](SOURCES.md) | Автоматически генерируемый список всех source ID и freshness policy |

## Для разработчика интеграции

| Документ | Содержание |
| --- | --- |
| [INTEGRATION_GUIDE.md](INTEGRATION_GUIDE.md) | Выбор REST/GraphQL, pagination, ошибки, кеширование и migration checklist |
| [API.md](API.md) | Полный REST reference: параметры, ответы и admin/ops endpoints |
| [GRAPHQL_API.md](GRAPHQL_API.md) | Query roots, фильтры, полная база, лимиты и error contract |
| [API_TOKENS.md](API_TOKENS.md) | Scopes, выпуск, проверка, ротация и отзыв токенов |
| [SDK.md](SDK.md) | TypeScript и C# clients, Persisted Queries и cursor helpers |
| [HSREPLAY_ARCHETYPE_DATABASE.md](HSREPLAY_ARCHETYPE_DATABASE.md) | Детальная HSReplay база архетипов и связанные endpoints |

## Для оператора

| Документ | Содержание |
| --- | --- |
| [../DEPLOY.md](../DEPLOY.md) | Установка, обновление, timers, migration и recovery |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Проверки по симптомам и безопасный порядок восстановления |
| [PARSER_PIPELINE.md](PARSER_PIPELINE.md) | Provider routing, quality gates, LKG, provisional и telemetry |
| [PROXY_AND_RELIABILITY.md](PROXY_AND_RELIABILITY.md) | Proxy/browser backends, structured logs и runbook |
| [SCRAPE_PROVIDERS.md](SCRAPE_PROVIDERS.md) | Cloud provider cascade, лимиты и circuit breaker |
| [SECURITY_AND_PARSING.md](SECURITY_AND_PARSING.md) | Threat model, секреты, premium sessions и hardening |
| [PARSER_CONTROL_API.md](PARSER_CONTROL_API.md) | Управление секциями, расписанием и durable parser runs |
| [CURRENT_PATCH_REFRESH.md](CURRENT_PATCH_REFRESH.md) | Безопасная публикация статистики сразу после патча |
| [PERFORMANCE.md](PERFORMANCE.md) | Безопасный benchmark, метрики latency и начальные бюджеты |

## Для разработчика проекта

| Документ | Содержание |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Границы, каталоги, основные потоки и deployment discipline |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | Рабочий процесс, проверки, документация и PR checklist |
| [../panel/docs/ADMIN_UI.md](../panel/docs/ADMIN_UI.md) | Контракты интерфейса панели и добавление модулей статистики |
| [../platform/README.md](../platform/README.md) | PostgreSQL shadow, schemas, импортеры и правила cutover |
| [../platform/docs/OPERATIONS.md](../platform/docs/OPERATIONS.md) | Runbook центральной data platform |
| [../platform/docs/decisions/](../platform/docs/decisions/) | ADR по PostgreSQL, панели и нормализованной статистике |

## Специализированные материалы

- [GAME_CHANGE_AUDIT.md](GAME_CHANGE_AUDIT.md) — ежедневный аудит изменений игры.
- [PARSER_IMPROVEMENT_PLAN.md](PARSER_IMPROVEMENT_PLAN.md) — исторический план
  развития парсеров; актуальное поведение проверяйте по коду и runbooks.
- [FRESH_99_IMPLEMENTATION_PLAN.md](FRESH_99_IMPLEMENTATION_PLAN.md) — текущий
  поэтапный план достижения честных 99% fresh-only и verified completeness.
- [STABILITY_ROADMAP.md](STABILITY_ROADMAP.md) — накопленный backlog
  стабилизации, а не описание уже гарантированного production‑поведения.

## Источники истины

Чтобы документация не расходилась с системой:

- source ID и количество источников берутся из `app.sources.SOURCES`;
- [SOURCES.md](SOURCES.md) генерируется
  `scripts/generate-source-catalog.py` и проверяется тестами;
- публичные HTTP‑контракты определяются routers и OpenAPI приложения;
- GraphQL schema находится в `app/graphql_api/`;
- PostgreSQL schema определяется migrations в `platform/sql/`;
- фактическое состояние production проверяется health/freshness/quality gates,
  а не статичным текстом в документации.

## Обозначения состояния данных

| Состояние | Что означает |
| --- | --- |
| `ok` / fresh | Новый кандидат прошёл проверки и опубликован |
| `provisional` | Разрешённый ограниченный срез сразу после патча |
| `ok_cached` / LKG | Новый refresh не прошёл, API сохраняет последний проверенный срез |
| `stale` | Набор существует, но старше freshness policy |
| `failed` | Полезный опубликованный результат отсутствует |

`GET /health` отвечает только на вопрос «работает ли API». Для ответа на вопрос
«все ли данные свежие и проверенные» нужны `freshness-check`, `quality-check` и
закрытые `/ops/*` endpoints.

## Wiki

Каталог [`wiki/`](../wiki/) содержит готовые Markdown‑страницы с боковой
навигацией для GitHub Wiki. Пока функция Wiki недоступна для текущего приватного
репозитория, эти страницы остаются читаемой repo-native Wiki. Порядок публикации
описан в [GITHUB_WIKI.md](GITHUB_WIKI.md).

## Обновление документации

При изменении публичного поведения обновите соответствующий reference или
руководство в том же PR. Не копируйте вручную полный реестр источников и не
вставляйте реальные токены, cookies, заголовки авторизации, payload production
или приватные сетевые адреса.
