# Авторизация и API‑токены

## Формат

```http
Authorization: Bearer khs_v1_<token-id>_<secret>
```

Plaintext secret возвращается только при выпуске. Система хранит digest и
безопасные operational metadata.

## Scopes

| Scope | Доступ |
| --- | --- |
| `database:read` | GraphQL `collections` и `records` |
| `admin` | `/admin/*`, `/ops/*`, premium health и refresh |
| `tokens:manage` | Выпуск, список и отзыв токенов |

Scopes независимы. Не выдавайте административные права consumer, который
только читает данные.

## Выпуск в панели

1. **Доступ → API‑токены**.
2. Одно понятное имя на один consumer.
3. Минимальные scopes.
4. Короткий практичный срок.
5. Сохранить показанный один раз secret.

## Проверка

```bash
curl -fsS https://api.kolodahearthstone.com/v1/auth/token \
  -H "Authorization: Bearer ${KHS_API_TOKEN}" | jq .
```

## Ротация

1. Выпустите замену с теми же минимальными scopes.
2. Обновите secret consumer.
3. Проверьте identity нового токена.
4. Отзовите старый token id.
5. Убедитесь, что старый credential возвращает `TOKEN_REVOKED`.

## Где хранить

- server-side secret manager;
- encrypted CI secret;
- restricted root/service file для operator tools.

Нельзя хранить в Git, URL, browser JavaScript, screenshot, issue, build log или
общем shell history.

## Ошибки

| HTTP | Код | Причина |
| ---: | --- | --- |
| 400 | `AMBIGUOUS_CREDENTIALS` | Bearer и compatibility header различаются |
| 401 | `INVALID_TOKEN` | Неверный формат/неизвестный credential |
| 401 | `TOKEN_EXPIRED` | Срок закончился |
| 401 | `TOKEN_REVOKED` | Токен отозван |
| 403 | `INSUFFICIENT_SCOPE` | Недостаточно прав |

Полная документация: [docs/API_TOKENS.md](../docs/API_TOKENS.md).
