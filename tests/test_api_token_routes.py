from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from app.api_tokens import ApiTokenStore
from app.main import app


def _configure_token_store(monkeypatch: Any, tmp_path: Path) -> ApiTokenStore:
    store = ApiTokenStore(database_path=tmp_path / "tokens.sqlite3")
    monkeypatch.setattr("app.api_tokens._default_store", store)
    monkeypatch.setattr("app.config.api_key", lambda: "bootstrap-secret")
    return store


def test_token_management_requires_a_management_credential(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _configure_token_store(monkeypatch, tmp_path)

    with TestClient(app) as client:
        missing = client.post(
            "/admin/api-tokens",
            json={
                "name": "Denied",
                "scopes": ["database:read"],
                "expires_in_days": 90,
            },
        )
        wrong = client.post(
            "/admin/api-tokens",
            headers={"Authorization": "Bearer wrong"},
            json={
                "name": "Denied",
                "scopes": ["database:read"],
                "expires_in_days": 90,
            },
        )

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "INVALID_TOKEN"
    assert missing.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401


def test_admin_can_issue_list_introspect_and_revoke_a_token(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    _configure_token_store(monkeypatch, tmp_path)
    bootstrap = {"Authorization": "Bearer bootstrap-secret"}

    with TestClient(app) as client:
        issued_response = client.post(
            "/admin/api-tokens",
            headers=bootstrap,
            json={
                "name": "Editorial integration",
                "scopes": ["database:read"],
                "expires_in_days": 90,
            },
        )
        assert issued_response.status_code == 201, issued_response.text
        issued = issued_response.json()["data"]
        assert issued["token"].startswith(f"khs_v1_{issued['id']}_")
        assert issued_response.json()["meta"] == {"secret_shown_once": True}

        listed_response = client.get("/admin/api-tokens", headers=bootstrap)
        assert listed_response.status_code == 200
        listed = listed_response.json()["data"]
        assert listed[0]["id"] == issued["id"]
        assert "token" not in listed[0]
        assert "token_hash" not in listed[0]

        token_headers = {"Authorization": f"Bearer {issued['token']}"}
        identity = client.get("/v1/auth/token", headers=token_headers)
        assert identity.status_code == 200
        assert identity.json()["data"] == {
            "id": issued["id"],
            "name": "Editorial integration",
            "scopes": ["database:read"],
            "expires_at": issued["expires_at"],
            "is_legacy": False,
        }

        denied_escalation = client.post(
            "/admin/api-tokens",
            headers=token_headers,
            json={
                "name": "Escalation attempt",
                "scopes": ["tokens:manage"],
                "expires_in_days": 90,
            },
        )
        assert denied_escalation.status_code == 403
        assert denied_escalation.json()["detail"]["code"] == "INSUFFICIENT_SCOPE"

        revoked = client.delete(f"/admin/api-tokens/{issued['id']}", headers=bootstrap)
        assert revoked.status_code == 204
        after_revoke = client.get("/v1/auth/token", headers=token_headers)
        assert after_revoke.status_code == 401
        assert after_revoke.json()["detail"]["code"] == "TOKEN_REVOKED"


def test_database_token_unlocks_full_graphql_without_admin_access(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    store = _configure_token_store(monkeypatch, tmp_path)
    issued = store.issue(
        name="GraphQL read only",
        scopes=["database:read"],
        expires_in_days=30,
        created_by="test",
    )

    from tests.test_graphql_api import FakeRepository

    fake = FakeRepository()
    monkeypatch.setattr("app.graphql_api.router.get_graphql_repository", lambda: fake)
    with TestClient(app) as client:
        graphql = client.post(
            "/v1/",
            headers={"Authorization": f"Bearer {issued.token}"},
            json={"query": "query { collections(limit: 1) { pageInfo { total } } }"},
        )
        admin = client.get(
            "/admin/parser-control",
            headers={"Authorization": f"Bearer {issued.token}"},
        )

    assert graphql.status_code == 200
    assert graphql.json()["data"]["collections"]["pageInfo"]["total"] == 54
    assert admin.status_code == 403
