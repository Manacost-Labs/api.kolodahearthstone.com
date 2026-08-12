# API tokens

`api.kolodahearthstone.com` uses scoped, expiring tokens for private database
and administration operations. Public catalogue, metadata and health queries
remain available without a token so browser integrations continue to work.

## Credential format

Send the credential in the standard HTTP header:

```http
Authorization: Bearer khs_v1_<token-id>_<secret>
```

`X-API-Key` remains accepted during migration. If both headers are sent, they
must contain the same value. A mismatch is rejected with
`AMBIGUOUS_CREDENTIALS`.

The plaintext secret is returned only by the issue operation. The database
stores its SHA-256 digest, an opaque public id and operational metadata. API
tokens contain 256 bits of cryptographic randomness, expire after at most 365
days and can be revoked immediately.

## Scopes

| Scope | Access |
| --- | --- |
| `database:read` | GraphQL `collections` and `records` for the complete database |
| `admin` | Existing `/admin/*`, `/ops/*` and premium health operations |
| `tokens:manage` | Issue, list and revoke API tokens |

Scopes are independent and follow least privilege. For example, a WordPress
integration that only reads GraphQL collections should receive only
`database:read`.

## Issue a token

The existing `HS_API_KEY` is a temporary bootstrap credential and has every
scope. Use it to issue the first `tokens:manage` token, then use scoped tokens
for routine operations.

```bash
curl -sS -X POST https://api.kolodahearthstone.com/admin/api-tokens \
  -H "Authorization: Bearer ${HS_API_KEY}" \
  -H "Content-Type: application/json" \
  --data '{
    "name": "WordPress production",
    "scopes": ["database:read"],
    "expires_in_days": 90
  }'
```

The response contains `data.token` exactly once. Save it directly in the
consumer's secret manager; do not put it in Git, JavaScript, a URL or logs.

Server operators can use the equivalent CLI:

```bash
python -m app.cli api-token issue \
  --name "WordPress production" \
  --scope database:read \
  --expires-in-days 90
```

## List and revoke

```bash
curl -sS https://api.kolodahearthstone.com/admin/api-tokens \
  -H "Authorization: Bearer ${TOKEN_MANAGER_KEY}"

curl -sS -X DELETE \
  https://api.kolodahearthstone.com/admin/api-tokens/TOKEN_ID \
  -H "Authorization: Bearer ${TOKEN_MANAGER_KEY}"
```

CLI equivalents:

```bash
python -m app.cli api-token list
python -m app.cli api-token revoke TOKEN_ID
```

List responses never include plaintext or token digests. `last_used_at` is
updated at most once every five minutes to avoid turning read traffic into
excessive database writes.

## Verify a token

```bash
curl -sS https://api.kolodahearthstone.com/v1/auth/token \
  -H "Authorization: Bearer ${KHS_API_TOKEN}"
```

The endpoint returns the token id, name, scopes and expiry. It never returns
the credential itself.

## Error contract

Authentication errors use an HTTP status and a stable code in `detail.code`:

| Status | Code | Meaning |
| --- | --- | --- |
| `400` | `AMBIGUOUS_CREDENTIALS` | Bearer and compatibility headers differ |
| `401` | `INVALID_TOKEN` | Credential is absent, malformed or unknown |
| `401` | `TOKEN_EXPIRED` | Token lifetime ended |
| `401` | `TOKEN_REVOKED` | Token was revoked |
| `403` | `INSUFFICIENT_SCOPE` | Token is valid but lacks the required scope |
| `422` | `INVALID_NAME`, `INVALID_SCOPES`, `INVALID_EXPIRY` | Issue request is invalid |

## Rotation

1. Issue a replacement with the same minimum scopes.
2. Update the consumer secret and verify `/v1/auth/token`.
3. Revoke the old token by id.
4. Confirm the old credential now returns `TOKEN_REVOKED`.

Do not revoke the only `tokens:manage` credential until its replacement has
been verified.
