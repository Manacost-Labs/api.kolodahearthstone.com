from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.api_tokens import ApiTokenStore
from app.cli import main, parse_args


def test_api_token_cli_contract() -> None:
    issue = parse_args(
        [
            "api-token",
            "issue",
            "--name",
            "WordPress",
            "--scope",
            "database:read",
            "--expires-in-days",
            "30",
        ]
    )
    assert issue.command == "api-token"
    assert issue.token_command == "issue"
    assert issue.name == "WordPress"
    assert issue.scope == ["database:read"]
    assert issue.expires_in_days == 30

    listing = parse_args(["api-token", "list"])
    assert listing.token_command == "list"

    revoke = parse_args(["api-token", "revoke", "abcdefghijkl"])
    assert revoke.token_id == "abcdefghijkl"

    leading_dash_revoke = parse_args(["api-token", "revoke", "-qZoyXiy9lY0"])
    assert leading_dash_revoke.token_id == "-qZoyXiy9lY0"


def test_api_token_cli_issues_lists_and_revokes_without_reprinting_secret(
    monkeypatch: Any,
    tmp_path: Path,
    capsys: Any,
) -> None:
    store = ApiTokenStore(database_path=tmp_path / "tokens.sqlite3")
    monkeypatch.setattr("app.api_tokens._default_store", store)

    assert (
        main(
            [
                "api-token",
                "issue",
                "--name",
                "CLI integration",
                "--scope",
                "database:read",
                "--expires-in-days",
                "30",
            ]
        )
        == 0
    )
    issued_output = json.loads(capsys.readouterr().out)
    token_id = issued_output["data"]["id"]
    plaintext = issued_output["data"]["token"]
    assert plaintext.startswith(f"khs_v1_{token_id}_")
    assert issued_output["meta"] == {"secret_shown_once": True}

    assert main(["api-token", "list"]) == 0
    list_output = json.loads(capsys.readouterr().out)
    assert list_output["data"][0]["id"] == token_id
    assert plaintext not in json.dumps(list_output)
    assert "token_hash" not in json.dumps(list_output)

    assert main(["api-token", "revoke", token_id]) == 0
    revoke_output = json.loads(capsys.readouterr().out)
    assert revoke_output == {"ok": True, "id": token_id, "revoked": True}
