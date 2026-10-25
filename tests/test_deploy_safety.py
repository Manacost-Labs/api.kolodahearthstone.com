from __future__ import annotations

import subprocess
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


def test_server_deploy_checks_host_timer_export_before_marking_success() -> None:
    script = (ROOT / "scripts" / "deploy-server.sh").read_text(encoding="utf-8")

    assert 'systemctl start "$HOST_TIMER_EXPORT_UNIT"' in script
    assert 'runuser -u "$HOST_SERVICE_USER" -- test -s "$HOST_TIMER_SNAPSHOT"' in script
    assert "exporter расписаний не прошёл post-deploy проверку" in script
    post_deploy = script[script.index('say "проверяю host-side exporter расписаний"') :]
    assert post_deploy.index("verify_host_timer_export") < post_deploy.index(
        'printf \'%s\\n\' "$COMMIT" > "$STAMP_FILE"'
    )


def test_server_deploy_rejects_the_dirty_tree_escape_hatch() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "deploy-server.sh"), "--allow-dirty"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "--allow-dirty больше не поддерживается" in result.stderr


def test_server_deploy_builds_only_origin_head_and_verifies_revision_label() -> None:
    script = (ROOT / "scripts" / "deploy-server.sh").read_text(encoding="utf-8")

    build = script.index('docker compose -f "$COMPOSE_FILE" build api')
    head_check = script.index('"$REVISION" == "$(git rev-parse "origin/$BRANCH")"')
    second_clean_check = script.index("require_clean_tree", head_check)
    label_check = script.index('"$built_revision" != "$REVISION"')
    restart = script.index('docker compose -f "$COMPOSE_FILE" up -d --no-build api')

    assert head_check < second_clean_check < build < label_check < restart
    assert 'HS_IMAGE_REVISION="$REVISION" docker compose' in script
    assert "ALLOW_DIRTY" not in script


def test_compose_is_the_single_config_and_labels_the_revision() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert not (ROOT / "docker-compose.prod.yml").exists()
    assert (
        "org.opencontainers.image.revision: ${HS_IMAGE_REVISION:-unknown}" in compose
    )


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


def test_server_deploy_is_the_single_path_for_platform_api_and_units() -> None:
    script = (ROOT / "scripts" / "deploy-server.sh").read_text(encoding="utf-8")

    pull = script.index('git pull --ff-only --quiet origin "$BRANCH"')
    platform = script.index("scripts/deploy-platform.sh \\")
    migrations = script.index('"$PLATFORM_TARGET/scripts/apply-migrations.sh" \\')
    build = script.index('docker compose -f "$COMPOSE_FILE" build api')
    health = script.index('say "проверяю здоровье: $HEALTH_URL"')
    units = script.index("scripts/install-docker-systemd.sh; then")
    exporter = script.index("if ! verify_host_timer_export; then")
    stamp = script.index('printf \'%s\\n\' "$COMMIT" > "$STAMP_FILE"')

    # Migrations before the new API starts; units only after it is healthy.
    assert pull < platform < migrations < build < health < units < exporter < stamp
    assert "--skip-platform) SKIP_PLATFORM=1" in script
    assert "--skip-units) SKIP_UNITS=1" in script
    assert 'flock -n 9 || die "другой деплой уже идёт' in script


def test_server_deploy_dry_run_plans_every_step_without_changes() -> None:
    script = (ROOT / "scripts" / "deploy-server.sh").read_text(encoding="utf-8")

    dry_run = script[
        script.index('if [[ "$DRY_RUN" -eq 1 ]]; then') : script.index(
            'git pull --ff-only --quiet origin "$BRANCH"'
        )
    ]
    assert "plan_migrations" in dry_run
    assert "unit_plan platform/systemd" in dry_run
    assert "unit_plan systemd 'hs-data-api-docker*'" in dry_run
    assert dry_run.rstrip().endswith("exit 0\nfi")
    # The pre-ledger runner ignored arguments and would apply migrations.
    assert "grep -q -- '--dry-run)'" in script


def test_agent_worktrees_never_dirty_the_deploy_checkout() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert "/.claude/" in gitignore
    assert ".claude" in dockerignore


def test_server_deploy_names_job_containers_of_other_revisions(tmp_path: Path) -> None:
    script = (ROOT / "scripts" / "deploy-server.sh").read_text(encoding="utf-8")
    function = script[
        script.index("report_old_job_containers() {") : script.index(
            "\n}\n", script.index("report_old_job_containers() {")
        )
        + 3
    ]
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\n" "$*" > "$DOCKER_ARGS_FILE"\n'
        "echo 'hs-data-api-docker-refresh-hsguru-deck-catalog|5d56addfeed|2 hours ago'\n"
        "echo 'hs-data-api-docker-run-convergence|newrev|10 seconds ago'\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    args_file = tmp_path / "args"

    result = subprocess.run(
        [
            "bash",
            "-c",
            f"set -euo pipefail\n{function}\nreport_old_job_containers newrev",
        ],
        capture_output=True,
        text=True,
        check=True,
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "COMPOSE_PROJECT": "hs-data-api",
            "DOCKER_ARGS_FILE": str(args_file),
        },
    )

    assert (
        "hs-data-api-docker-refresh-hsguru-deck-catalog (ревизия 5d56add"
        in result.stderr
    )
    assert "run-convergence" not in result.stderr
    docker_args = args_file.read_text(encoding="utf-8")
    assert "label=com.docker.compose.oneoff=True" in docker_args
    assert "label=com.docker.compose.project=hs-data-api" in docker_args
    # Called in the dry-run plan and before the deploy is marked successful.
    assert script.count("report_old_job_containers ") == 2
    stamp = script.index('printf \'%s\\n\' "$COMMIT" > "$STAMP_FILE"')
    assert script.rindex('report_old_job_containers "$REVISION"') < stamp
