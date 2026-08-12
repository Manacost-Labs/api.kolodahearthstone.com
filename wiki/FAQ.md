# Частые вопросы

## Это одна система или несколько API?

Канонический host один: `api.kolodahearthstone.com`. На нём работают веб‑панель,
REST compatibility endpoints и GraphQL `/v1/`.

## Где находится вся база?

Для человека — в веб‑панели. Для сервиса — в GraphQL typed queries или через
`collections`/`records` с `database:read`. Raw parser snapshots доступны через
`/datasets/{source_id}`.

## Почему `/health` зелёный, а источник старый?

`/health` проверяет процесс API. Freshness каждого источника — отдельное
состояние. Смотрите `/sources`, панель «Обзор» и operator freshness-check.

## Что такое LKG?

Last-known-good. Если новый ответ не проходит проверки, система оставляет
предыдущий валидный snapshot доступным и показывает деградацию в status.

## Нужен ли токен для публичной статистики?

Большинство public read-only endpoints и typed GraphQL queries доступны без
токена. Полная PostgreSQL‑база и admin operations защищены scopes.

## Какой scope нужен сайту или боту?

Обычно `database:read`, если нужны `collections`/`records`. Если используются
только public endpoints, токен может не понадобиться.

## Можно ли восстановить потерянный secret токена?

Нет. Выпустите замену, проверьте её и отзовите потерянный токен.

## REST или GraphQL?

REST — готовый dataset. GraphQL — точные поля, связи и полная central database.

## Можно ли отключить старые домены?

Только после замены origins во всех consumers, smoke checks новых paths/media и
проверки отсутствия обязательного legacy traffic за согласованное окно.

## Почему source count не пишется вручную во всех документах?

Потому что он меняется. Авторитетный список генерируется из
`app.sources.SOURCES` в `docs/SOURCES.md`.

## Где сообщить об ошибке?

Обычная ошибка — issue репозитория с шагами воспроизведения и без secrets.
Security problem — приватно владельцу, не в открытом issue.
