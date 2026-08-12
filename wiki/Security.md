# Безопасность

## Что нельзя коммитить

- `.env` и runtime config;
- API/provider tokens;
- OAuth credentials;
- cookies и browser storage state;
- private proxy URLs;
- production datasets и database dumps;
- backups и log fragments с payload/headers.

## Доступ к API

- public endpoints только read-only;
- central database: `database:read`;
- refresh/ops: `admin`;
- token lifecycle: `tokens:manage`;
- external orchestrator: отдельный credential и narrow endpoints.

Используйте least privilege и один token на один consumer.

## Доступ к панели

Панель защищена GitHub OAuth, allowlist по immutable user identity, session
expiry, CSRF, one-time form nonce и rate limit выпуска токенов. Manager token
хранится вне web root и не передаётся браузеру.

## Парсеры

- не доверяйте HTTP 200 без проверки body;
- challenge/login page не является dataset;
- provider errors санитизируются;
- retries bounded и учитывают budget;
- candidate не публикуется без deterministic validation;
- AI‑диагностика не может override gate;
- raw HTML/cookies/tokens не передаются во внешний AI request.

## Данные и база

- snapshots пишутся атомарно;
- PostgreSQL не открыт публично;
- sync в shadow period однонаправленный;
- restore проверяется в отдельной базе;
- credentials/backups имеют restricted permissions.

## Для разработчика

```bash
make check
make security
```

Security findings сначала анализируются. Не выполняйте массовые автоматические
исправления, которые меняют unrelated behavior.

Полная threat model и hardening:
[docs/SECURITY_AND_PARSING.md](../docs/SECURITY_AND_PARSING.md).
