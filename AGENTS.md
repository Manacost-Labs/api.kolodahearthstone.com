# HS Data API — правила для AI-агентов

## Назначение

FastAPI-сервис собирает и нормализует данные Hearthstone из внешних источников. Исходники находятся в `app/`, HTTP routers — в `app/routers/`, tests — в `tests/`. Production/runtime-данные и credentials не являются частью исходников.

## Работа с кодом

- Перед чтением связей и изменением Python-кода использовать CodeGraph.
- Для глубокого аудита свежести и надёжности использовать `.agents/skills/audit-parser-system/SKILL.md`.
- Не выполнять реальные premium/API запросы в тестах; внешнюю сеть и время подменять fixtures/mocks.
- Не менять contracts источников, схемы SQLite и publication gates без regression tests.
- Не читать и не выводить `.env`, proxy credentials, cookies и auth tokens.

## Проверка

- Один раз: `make setup`.
- Обязательный gate: `make check`.
- Текущий Ruff debt виден через `make lint-report`; не исправлять его массово вместе с функциональной задачей.
- Security baseline: `make security`.
