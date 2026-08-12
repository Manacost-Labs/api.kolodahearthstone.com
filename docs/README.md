# Документация HS Data API

Начните с [архитектуры](ARCHITECTURE.md), если нужно понять расположение кода,
путь данных и границу между source и production. Полный реестр источников
генерируется в [SOURCES.md](SOURCES.md), а пользовательские наборы данных и
поля описаны в [DATA_CATALOG.md](DATA_CATALOG.md).

## Основные разделы

| Документ | Назначение |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Каталоги, компоненты, потоки данных, запуск и deployment. |
| [API.md](API.md) | REST endpoints, авторизация, ответы и примеры. |
| [API_TOKENS.md](API_TOKENS.md) | Выпуск, scopes, проверка, ротация и отзыв API-токенов. |
| [SOURCES.md](SOURCES.md) | Генерируемый технический реестр всех источников. |
| [DATA_CATALOG.md](DATA_CATALOG.md) | Какие данные доступны пользователю и какие поля возвращаются. |
| [SECURITY_AND_PARSING.md](SECURITY_AND_PARSING.md) | Секреты, proxy, premium-доступ и защита парсеров. |
| [PROXY_AND_RELIABILITY.md](PROXY_AND_RELIABILITY.md) | Ротация backend/proxy и обработка отказов. |
| [PARSER_CONTROL_API.md](PARSER_CONTROL_API.md) | Управление секциями и расписанием парсеров. |
| [STABILITY_ROADMAP.md](STABILITY_ROADMAP.md) | Приоритетные шаги по стабилизации проекта. |

## Специализированные разделы

- [HSREPLAY_ARCHETYPE_DATABASE.md](HSREPLAY_ARCHETYPE_DATABASE.md) — локальная
  база архетипов HSReplay.
- [CURRENT_PATCH_REFRESH.md](CURRENT_PATCH_REFRESH.md) — обновление данных после
  патча Hearthstone.
- [GAME_CHANGE_AUDIT.md](GAME_CHANGE_AUDIT.md) — аудит изменений игры.
- [PARSER_IMPROVEMENT_PLAN.md](PARSER_IMPROVEMENT_PLAN.md) — более широкий
  исторический план развития парсеров.

Deployment описан в корневом [DEPLOY.md](../DEPLOY.md). Команды для локальной
проверки проекта находятся в корневом `Makefile`.
