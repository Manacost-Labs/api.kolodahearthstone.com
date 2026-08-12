from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .db import get_db_path

TOKEN_PREFIX = "khs_v1"
TOKEN_PATTERN = re.compile(
    rf"^{TOKEN_PREFIX}_(?P<id>[A-Za-z0-9_-]{{12}})_(?P<secret>[A-Za-z0-9_-]{{40,64}})$"
)
VALID_SCOPES = frozenset(
    {
        "database:read",
        "admin",
        "tokens:manage",
    }
)
MAX_TOKEN_LIFETIME_DAYS = 365
LAST_USED_WRITE_INTERVAL = timedelta(minutes=5)


class ApiTokenError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 401) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ApiTokenPrincipal:
    id: str
    name: str
    scopes: frozenset[str]
    expires_at: datetime | None
    is_legacy: bool = False

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True)
class ApiTokenMetadata:
    id: str
    name: str
    scopes: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_by: str
    revoked_by: str | None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.now(UTC)


@dataclass(frozen=True)
class IssuedApiToken(ApiTokenMetadata):
    token: str


def _format_time(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _digest_token(token: str) -> str:
    # API tokens contain 256 bits of cryptographic randomness, so a fast digest
    # is safe for indexed verification while ensuring plaintext is never stored.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    normalized = tuple(
        sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    )
    if not normalized or any(scope not in VALID_SCOPES for scope in normalized):
        raise ApiTokenError(
            "INVALID_SCOPES",
            "At least one supported scope is required",
            status_code=422,
        )
    return normalized


def _validate_name(name: str) -> str:
    normalized = str(name).strip()
    if (
        not normalized
        or len(normalized) > 80
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ApiTokenError(
            "INVALID_NAME",
            "Token name must contain 1 to 80 printable characters",
            status_code=422,
        )
    return normalized


def _validate_actor(actor: str) -> str:
    normalized = str(actor).strip()
    if not normalized or len(normalized) > 120:
        raise ApiTokenError("INVALID_ACTOR", "Token actor is invalid", status_code=422)
    return normalized


class ApiTokenStore:
    def __init__(
        self,
        database_path: Path | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = database_path or get_db_path()
        self._now = now or (lambda: datetime.now(UTC))
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.database_path), timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def _ensure_schema(self) -> None:
        connection = self._connect()
        try:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS api_tokens (
                        id TEXT PRIMARY KEY,
                        token_hash TEXT NOT NULL UNIQUE,
                        name TEXT NOT NULL,
                        scopes_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        last_used_at TEXT,
                        revoked_at TEXT,
                        created_by TEXT NOT NULL,
                        revoked_by TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_api_tokens_active "
                    "ON api_tokens(revoked_at, expires_at)"
                )
        finally:
            connection.close()

    def issue(
        self,
        *,
        name: str,
        scopes: Iterable[str],
        expires_in_days: int,
        created_by: str,
    ) -> IssuedApiToken:
        normalized_name = _validate_name(name)
        normalized_scopes = _normalize_scopes(scopes)
        actor = _validate_actor(created_by)
        if expires_in_days < 1 or expires_in_days > MAX_TOKEN_LIFETIME_DAYS:
            raise ApiTokenError(
                "INVALID_EXPIRY",
                f"Token lifetime must be between 1 and {MAX_TOKEN_LIFETIME_DAYS} days",
                status_code=422,
            )

        now = self._now().astimezone(UTC).replace(microsecond=0)
        expires_at = now + timedelta(days=expires_in_days)
        connection = self._connect()
        try:
            for _attempt in range(3):
                token_id = secrets.token_urlsafe(9)
                token = f"{TOKEN_PREFIX}_{token_id}_{secrets.token_urlsafe(32)}"
                try:
                    with connection:
                        connection.execute(
                            """
                            INSERT INTO api_tokens (
                                id, token_hash, name, scopes_json, created_at,
                                expires_at, created_by
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                token_id,
                                _digest_token(token),
                                normalized_name,
                                json.dumps(normalized_scopes, separators=(",", ":")),
                                _format_time(now),
                                _format_time(expires_at),
                                actor,
                            ),
                        )
                    return IssuedApiToken(
                        id=token_id,
                        token=token,
                        name=normalized_name,
                        scopes=normalized_scopes,
                        created_at=now,
                        expires_at=expires_at,
                        last_used_at=None,
                        revoked_at=None,
                        created_by=actor,
                        revoked_by=None,
                    )
                except sqlite3.IntegrityError:
                    continue
        finally:
            connection.close()

        raise RuntimeError("Could not allocate a unique API token identifier")

    def authenticate(
        self,
        token: str,
        *,
        required_scope: str | None = None,
    ) -> ApiTokenPrincipal:
        match = TOKEN_PATTERN.fullmatch(token)
        if match is None:
            raise ApiTokenError("INVALID_TOKEN", "Missing or invalid API token")

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM api_tokens WHERE id = ?",
                (match.group("id"),),
            ).fetchone()
            if row is None or not secrets.compare_digest(
                str(row["token_hash"]),
                _digest_token(token),
            ):
                raise ApiTokenError("INVALID_TOKEN", "Missing or invalid API token")

            now = self._now().astimezone(UTC).replace(microsecond=0)
            expires_at = _parse_time(str(row["expires_at"]))
            revoked_at = _parse_time(row["revoked_at"])
            if revoked_at is not None:
                raise ApiTokenError("TOKEN_REVOKED", "API token has been revoked")
            if expires_at is None or expires_at <= now:
                raise ApiTokenError("TOKEN_EXPIRED", "API token has expired")

            scopes = frozenset(json.loads(str(row["scopes_json"])))
            if required_scope and required_scope not in scopes:
                raise ApiTokenError(
                    "INSUFFICIENT_SCOPE",
                    f"API token requires the {required_scope} scope",
                    status_code=403,
                )

            last_used_at = _parse_time(row["last_used_at"])
            if last_used_at is None or now - last_used_at >= LAST_USED_WRITE_INTERVAL:
                with connection:
                    connection.execute(
                        "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                        (_format_time(now), str(row["id"])),
                    )

            return ApiTokenPrincipal(
                id=str(row["id"]),
                name=str(row["name"]),
                scopes=scopes,
                expires_at=expires_at,
            )
        finally:
            connection.close()

    def list_tokens(self) -> list[ApiTokenMetadata]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT id, name, scopes_json, created_at, expires_at,
                       last_used_at, revoked_at, created_by, revoked_by
                FROM api_tokens
                ORDER BY created_at DESC, id DESC
                """
            ).fetchall()
        finally:
            connection.close()
        return [self._metadata_from_row(row) for row in rows]

    def get(self, token_id: str) -> ApiTokenMetadata | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT id, name, scopes_json, created_at, expires_at,
                       last_used_at, revoked_at, created_by, revoked_by
                FROM api_tokens
                WHERE id = ?
                """,
                (token_id,),
            ).fetchone()
        finally:
            connection.close()
        return self._metadata_from_row(row) if row is not None else None

    def revoke(self, token_id: str, *, revoked_by: str) -> bool:
        actor = _validate_actor(revoked_by)
        now = _format_time(self._now().astimezone(UTC).replace(microsecond=0))
        connection = self._connect()
        try:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE api_tokens
                    SET revoked_at = COALESCE(revoked_at, ?),
                        revoked_by = COALESCE(revoked_by, ?)
                    WHERE id = ?
                    """,
                    (now, actor, token_id),
                )
            return cursor.rowcount > 0
        finally:
            connection.close()

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> ApiTokenMetadata:
        created_at = _parse_time(str(row["created_at"]))
        expires_at = _parse_time(str(row["expires_at"]))
        if created_at is None or expires_at is None:
            raise RuntimeError("API token timestamps are invalid")
        return ApiTokenMetadata(
            id=str(row["id"]),
            name=str(row["name"]),
            scopes=tuple(json.loads(str(row["scopes_json"]))),
            created_at=created_at,
            expires_at=expires_at,
            last_used_at=_parse_time(row["last_used_at"]),
            revoked_at=_parse_time(row["revoked_at"]),
            created_by=str(row["created_by"]),
            revoked_by=str(row["revoked_by"]) if row["revoked_by"] else None,
        )


_default_store: ApiTokenStore | None = None


def get_api_token_store() -> ApiTokenStore:
    global _default_store
    if _default_store is None:
        _default_store = ApiTokenStore()
    return _default_store


def extract_api_token(authorization: str | None, x_api_key: str | None) -> str:
    bearer: str | None = None
    if authorization:
        scheme, separator, value = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not value.strip():
            raise ApiTokenError(
                "INVALID_AUTHORIZATION_HEADER",
                "Authorization header must use the Bearer scheme",
            )
        bearer = value.strip()

    legacy_header = x_api_key.strip() if x_api_key else None
    if bearer and legacy_header and not secrets.compare_digest(bearer, legacy_header):
        raise ApiTokenError(
            "AMBIGUOUS_CREDENTIALS",
            "Authorization and X-API-Key credentials do not match",
            status_code=400,
        )
    supplied = bearer or legacy_header
    if not supplied:
        raise ApiTokenError("INVALID_TOKEN", "Missing or invalid API token")
    return supplied


def authenticate_api_token(
    token: str,
    *,
    required_scope: str | None,
    legacy_key: str | None,
    store: ApiTokenStore | None = None,
) -> ApiTokenPrincipal:
    if legacy_key and secrets.compare_digest(token, legacy_key):
        return ApiTokenPrincipal(
            id="legacy-bootstrap",
            name="Legacy bootstrap key",
            scopes=VALID_SCOPES,
            expires_at=None,
            is_legacy=True,
        )
    return (store or get_api_token_store()).authenticate(
        token,
        required_scope=required_scope,
    )


def api_token_http_exception(error: ApiTokenError):
    from fastapi import HTTPException

    headers = {"WWW-Authenticate": "Bearer"} if error.status_code == 401 else None
    return HTTPException(
        status_code=error.status_code,
        detail={"code": error.code, "message": error.message},
        headers=headers,
    )
