# Быстрый старт

## Посмотреть данные

1. Откройте <https://api.kolodahearthstone.com/>.
2. Войдите через разрешённый GitHub‑аккаунт.
3. Выберите **«Обзор и мета»**.
4. На **«Обзоре»** проверьте источники и время обновления.
5. Переключайтесь между метой, архетипами, картами, Arena и BG.
6. Нажмите **«Подробнее»**, чтобы увидеть полный объект записи.

## Проверить API

```bash
curl -fsS https://api.kolodahearthstone.com/health | jq .
curl -fsS https://api.kolodahearthstone.com/sources | jq .
```

Получить dataset:

```bash
curl -fsS \
  https://api.kolodahearthstone.com/datasets/hsguru_meta_standard_legend \
  | jq '.data.structured'
```

## Первый GraphQL‑запрос

```bash
curl -fsS https://api.kolodahearthstone.com/v1/graphql \
  -H 'Content-Type: application/json' \
  --data '{"query":"query { health { status databaseConnected sourceCount latestSyncAt } }"}' \
  | jq .
```

GraphQL endpoint принимает только `POST`. Browser IDE и production
introspection UI отключены.

## Выпустить токен

В панели откройте **Доступ → API‑токены**:

1. Назовите конкретный consumer.
2. Для чтения базы выберите `database:read`.
3. Ограничьте срок действия.
4. Скопируйте секрет — второй раз он не показывается.
5. Проверьте токен через `/v1/auth/token`.

```bash
curl -fsS https://api.kolodahearthstone.com/v1/auth/token \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" | jq .
```

## Подключить приложение

- задайте единый origin `https://api.kolodahearthstone.com`;
- храните токен только на backend;
- задайте timeout, bounded retry и обработку `429`;
- обрабатывайте pagination;
- в GraphQL проверяйте и HTTP status, и `errors[]`;
- различайте fresh, provisional, LKG и stale;
- удалите обращения к старым доменам только после проверки трафика.

Подробно: [REST и GraphQL API](API.md) и
[Авторизация](Authentication.md).

## Локальный запуск

```bash
git clone https://github.com/Manacost-Labs/api.kolodahearthstone.com.git
cd api.kolodahearthstone.com
make setup
make check
```

```bash
HS_API_DATA_DIR="$PWD/data" \
HS_API_BIND_HOST="127.0.0.1" \
.venv/bin/python -m app.server
```

Полный refresh не нужен для первого запуска и может расходовать provider
credits. Используйте fixtures/tests, пока явно не настроены внешние зависимости.
