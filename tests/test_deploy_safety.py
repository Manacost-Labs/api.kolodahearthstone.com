from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_local_deploy_preserves_runtime_data_and_secrets() -> None:
    script = (ROOT / "scripts" / "deploy-local.sh").read_text(encoding="utf-8")

    assert "rsync -av --delete" in script
    for protected_path in (
        "data",
        "'.env*'",
        "'.credentials*'",
        ".git",
    ):
        assert f"--exclude {protected_path}" in script


def test_server_deploy_keeps_tracked_app_code_readable_by_host_services() -> None:
    script = (ROOT / "scripts" / "deploy-server.sh").read_text(encoding="utf-8")

    assert "umask 0022" in script
    assert "git ls-files -z -- app" in script
    assert 'chmod a+r -- "$path"' in script
    assert 'runuser -u "$HOST_SERVICE_USER" -- test -r' in script
    assert "chmod -R" not in script


def test_token_administration_has_a_dedicated_rate_limit() -> None:
    vhost = (ROOT / "deploy" / "nginx" / "api.kolodahearthstone.com.conf").read_text(
        encoding="utf-8"
    )
    zones = (ROOT / "deploy" / "nginx" / "koloda-api-token-rate-limit.conf").read_text(
        encoding="utf-8"
    )

    assert "zone=koloda_api_token_auth" in zones
    assert "location ^~ /admin/api-tokens" in vhost
    assert "limit_req zone=koloda_api_token_auth" in vhost
    assert "limit_req_status 429" in vhost
    assert "client_max_body_size 16k" in vhost
