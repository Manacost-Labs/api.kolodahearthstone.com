from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.api_tokens import (
    ApiTokenError,
    ApiTokenStore,
    authenticate_api_token,
    extract_api_token,
)

NOW = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)


def _store(tmp_path: Path, clock: list[datetime] | None = None) -> ApiTokenStore:
    current = clock or [NOW]
    return ApiTokenStore(
        database_path=tmp_path / "tokens.sqlite3",
        now=lambda: current[0],
    )


def test_issue_returns_secret_once_and_persists_only_a_digest(tmp_path: Path) -> None:
    store = _store(tmp_path)

    issued = store.issue(
        name="WordPress production",
        scopes=["database:read"],
        expires_in_days=90,
        created_by="Zulut30",
    )

    assert issued.token.startswith(f"khs_v1_{issued.id}_")
    assert issued.name == "WordPress production"
    assert issued.scopes == ("database:read",)
    assert issued.expires_at == NOW + timedelta(days=90)

    connection = sqlite3.connect(tmp_path / "tokens.sqlite3")
    try:
        stored = connection.execute(
            "SELECT token_hash, name, scopes_json FROM api_tokens WHERE id = ?",
            (issued.id,),
        ).fetchone()
    finally:
        connection.close()

    assert stored is not None
    assert issued.token not in stored[0]
    assert len(stored[0]) == 64
    assert stored[1] == "WordPress production"
    assert stored[2] == '["database:read"]'

    listed = store.list_tokens()
    assert listed[0].id == issued.id
    assert not hasattr(listed[0], "token")
    assert not hasattr(listed[0], "token_hash")


def test_authentication_enforces_scope_expiry_and_revocation(tmp_path: Path) -> None:
    clock = [NOW]
    store = _store(tmp_path, clock)
    issued = store.issue(
        name="Read-only consumer",
        scopes=["database:read"],
        expires_in_days=1,
        created_by="Zulut30",
    )

    principal = store.authenticate(issued.token, required_scope="database:read")
    assert principal.id == issued.id
    assert principal.has_scope("database:read")

    with pytest.raises(ApiTokenError) as insufficient:
        store.authenticate(issued.token, required_scope="admin")
    assert insufficient.value.code == "INSUFFICIENT_SCOPE"
    assert insufficient.value.status_code == 403

    clock[0] = NOW + timedelta(days=2)
    with pytest.raises(ApiTokenError) as expired:
        store.authenticate(issued.token)
    assert expired.value.code == "TOKEN_EXPIRED"

    clock[0] = NOW
    assert store.revoke(issued.id, revoked_by="Zulut30") is True
    with pytest.raises(ApiTokenError) as revoked:
        store.authenticate(issued.token)
    assert revoked.value.code == "TOKEN_REVOKED"
    assert store.revoke("missing-token", revoked_by="Zulut30") is False


def test_malformed_and_ambiguous_credentials_fail_closed(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert extract_api_token("Bearer one", None) == "one"
    assert extract_api_token(None, "legacy") == "legacy"

    with pytest.raises(ApiTokenError) as conflict:
        extract_api_token("Bearer one", "two")
    assert conflict.value.code == "AMBIGUOUS_CREDENTIALS"

    with pytest.raises(ApiTokenError) as malformed_header:
        extract_api_token("Basic abc", None)
    assert malformed_header.value.code == "INVALID_AUTHORIZATION_HEADER"

    with pytest.raises(ApiTokenError) as malformed_token:
        store.authenticate("khs_v1_bad")
    assert malformed_token.value.code == "INVALID_TOKEN"


def test_legacy_bootstrap_key_remains_compatible_and_timing_safe(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    principal = authenticate_api_token(
        "current-static-key",
        required_scope="tokens:manage",
        legacy_key="current-static-key",
        store=store,
    )
    assert principal.is_legacy is True
    assert principal.has_scope("tokens:manage")

    with pytest.raises(ApiTokenError) as denied:
        authenticate_api_token(
            "wrong",
            required_scope="tokens:manage",
            legacy_key="current-static-key",
            store=store,
        )
    assert denied.value.code == "INVALID_TOKEN"


@pytest.mark.parametrize(
    ("name", "scopes", "days", "code"),
    [
        ("", ["database:read"], 90, "INVALID_NAME"),
        ("x" * 81, ["database:read"], 90, "INVALID_NAME"),
        ("consumer", [], 90, "INVALID_SCOPES"),
        ("consumer", ["root"], 90, "INVALID_SCOPES"),
        ("consumer", ["database:read"], 0, "INVALID_EXPIRY"),
        ("consumer", ["database:read"], 366, "INVALID_EXPIRY"),
    ],
)
def test_issue_validates_all_external_input(
    tmp_path: Path,
    name: str,
    scopes: list[str],
    days: int,
    code: str,
) -> None:
    with pytest.raises(ApiTokenError) as error:
        _store(tmp_path).issue(
            name=name,
            scopes=scopes,
            expires_in_days=days,
            created_by="Zulut30",
        )
    assert error.value.code == code
