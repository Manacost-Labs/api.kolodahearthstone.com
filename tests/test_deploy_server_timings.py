"""Поведенческие тесты deploy-server.sh: тайминги фаз, итог в journald, откат.

Скрипт запускается целиком в песочнице: настоящий git (локальный bare origin и
клон-«прод» во временном каталоге), а docker, curl, systemctl, runuser, id и
logger — заглушки в PATH. Настоящих Docker, сервисов и сети нет.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "deploy-server.sh"

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]

STUBS = {
    # Каждая заглушка дописывает свой вызов в $STUB_LOG, чтобы тест видел,
    # что именно запускал скрипт.
    "docker": r"""#!/usr/bin/env bash
echo "docker $*" >> "$STUB_LOG"
case "$1 $2" in
  "image inspect")
    if [[ "$*" == *"--format"* ]]; then echo "$STUB_BUILT_REVISION"; fi
    exit 0 ;;
esac
exit 0
""",
    "curl": r"""#!/usr/bin/env bash
echo "curl $*" >> "$STUB_LOG"
printf '%s' "${STUB_HEALTH_CODE:-200}"
""",
    "systemctl": r"""#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"
[[ "$1" == start && "${STUB_EXPORTER_FAILS:-0}" == 1 ]] && exit 1
exit 0
""",
    "runuser": r"""#!/usr/bin/env bash
# runuser -u <user> -- <команда ...>: выполняем команду от текущего пользователя
shift 2; [[ "$1" == -- ]] && shift
exec "$@"
""",
    "id": r"""#!/usr/bin/env bash
if [[ "$1" == -u ]]; then echo 0; fi
exit 0
""",
    "logger": r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_JOURNAL"
""",
    "sleep": "#!/usr/bin/env bash\nexit 0\n",
}


@dataclass
class Sandbox:
    root: Path
    prod: Path
    origin: Path
    old_commit: str
    new_commit: str
    new_revision: str

    @property
    def stub_log(self) -> Path:
        return self.root / "stub.log"

    @property
    def journal(self) -> Path:
        return self.root / "journal.log"

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        full_env = {
            "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
            "HOME": str(self.root),
            "INSTALL_DIR": str(self.prod),
            "DEPLOY_LOCK_FILE": str(self.root / "deploy.lock"),
            "SYSTEMD_DIR": str(self.root / "systemd"),
            "HEALTH_RETRIES": "3",
            "HEALTH_DELAY": "0",
            "STUB_LOG": str(self.stub_log),
            "STUB_JOURNAL": str(self.journal),
            "STUB_BUILT_REVISION": self.new_revision,
            **env,
        }
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            check=False,
            env=full_env,
            timeout=60,
        )

    def stub_calls(self) -> list[str]:
        return self.stub_log.read_text().splitlines() if self.stub_log.exists() else []

    def journal_lines(self) -> list[str]:
        return self.journal.read_text().splitlines() if self.journal.exists() else []


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        [*GIT, *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in STUBS.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)

    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    prod = tmp_path / "prod"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    (seed / "app").mkdir()
    (seed / "app" / "systemd_timer_export.py").write_text("# stub\n")
    (seed / "scripts").mkdir()
    (seed / "scripts" / "install-docker-systemd.sh").write_text(
        '#!/usr/bin/env bash\necho "install-units" >> "$STUB_LOG"\n'
        'exit "${STUB_UNITS_EXIT:-0}"\n'
    )
    (seed / "scripts" / "install-docker-systemd.sh").chmod(0o755)
    (seed / ".gitignore").write_text("data/\n.deployed-commit\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "old")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(prod))
    (prod / "data").mkdir()
    (prod / "data" / "parser-control-systemd.json").write_text("{}")

    (seed / "app" / "feature.py").write_text("# new\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "new")
    _git(seed, "push", "-q", "origin", "main")

    return Sandbox(
        root=tmp_path,
        prod=prod,
        origin=origin,
        old_commit=_git(prod, "rev-parse", "--short", "HEAD"),
        new_commit=_git(seed, "rev-parse", "--short", "HEAD"),
        new_revision=_git(seed, "rev-parse", "HEAD"),
    )


def _summary(sb: Sandbox) -> dict[str, str]:
    lines = sb.journal_lines()
    assert len(lines) == 1, f"ожидалась одна строка journald, а не {lines}"
    line = lines[0]
    assert line.startswith("-t hs-data-api-deploy -- ")
    return dict(re.findall(r"(\w+)=(\S+)", line))


ALL_PHASES = ("fetch", "platform", "build", "up", "health", "units")


def test_successful_deploy_prints_every_phase_and_logs_one_summary(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("--skip-platform")

    assert result.returncode == 0, result.stderr
    for label in (
        "получение изменений",
        "платформа и миграции",
        "сборка образа",
        "перезапуск",
        "ожидание здоровья",
        "юниты и exporter",
    ):
        assert re.search(rf"==> фаза {label}: \d+\.\d с", result.stdout), label
    assert re.search(r"==> итого: \d+\.\d с, результат: ok", result.stdout)

    summary = _summary(sandbox)
    assert summary["result"] == "ok"
    assert summary["exit"] == "0"
    assert summary["commit"] == sandbox.new_commit
    assert summary["old"] == sandbox.old_commit
    assert "failed_at" not in summary
    for phase in ALL_PHASES:
        assert re.fullmatch(r"\d+\.\ds", summary[phase]), phase
    assert (sandbox.prod / ".deployed-commit").read_text().strip() == sandbox.new_commit
    assert f"docker image tag hs-data-api:local hs-data-api:rollback-{sandbox.old_commit}" in sandbox.stub_calls()


def test_unhealthy_api_rolls_back_exits_1_and_journals_rollback(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("--skip-platform", STUB_HEALTH_CODE="503")

    assert result.returncode == 1
    assert "откатываюсь" in result.stderr
    calls = sandbox.stub_calls()
    # Откат вернул прежний образ под :local и перезапустил API без пересборки.
    tag = f"docker image tag hs-data-api:rollback-{sandbox.old_commit} hs-data-api:local"
    restart = "docker compose -f docker-compose.yml up -d --no-deps --no-build api"
    assert tag in calls
    assert restart in calls
    assert calls.index(tag) > max(
        i for i, c in enumerate(calls) if c.startswith("curl ")
    )
    # Все попытки здоровья сделаны, и до юнитов дело не дошло.
    assert sum(c.startswith("curl ") for c in calls) == 3
    assert "install-units" not in calls
    assert not (sandbox.prod / ".deployed-commit").exists()

    summary = _summary(sandbox)
    assert summary["result"] == "rollback"
    assert summary["exit"] == "1"
    assert summary["failed_at"] == "health"
    assert summary["commit"] == sandbox.new_commit
    assert "units" not in summary
    assert "результат: rollback" in result.stdout


def test_failing_host_exporter_rolls_back_and_journals_rollback(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("--skip-platform", STUB_EXPORTER_FAILS="1")

    assert result.returncode == 1
    assert "exporter расписаний не прошёл" in result.stderr
    calls = sandbox.stub_calls()
    assert (
        f"docker image tag hs-data-api:rollback-{sandbox.old_commit} hs-data-api:local"
        in calls
    )
    assert not (sandbox.prod / ".deployed-commit").exists()

    summary = _summary(sandbox)
    assert summary["result"] == "rollback"
    assert summary["failed_at"] == "units"
    assert summary["exit"] == "1"


def test_failing_units_install_is_journaled_as_fail_without_rollback(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("--skip-platform", STUB_UNITS_EXIT="1")

    assert result.returncode == 1
    assert "install-docker-systemd.sh не прошёл" in result.stderr
    after_units = sandbox.stub_calls()[sandbox.stub_calls().index("install-units") :]
    assert not any(c.startswith("docker image tag") for c in after_units)
    assert not any("--no-deps" in c for c in after_units)
    summary = _summary(sandbox)
    assert summary["result"] == "fail"
    assert summary["failed_at"] == "units"
    assert summary["exit"] == "1"


def test_wrong_image_revision_stops_before_restart_and_journals_fail(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("--skip-platform", STUB_BUILT_REVISION="deadbeef")

    assert result.returncode == 1
    assert "revision=deadbeef" in result.stderr
    assert not any("up -d" in c for c in sandbox.stub_calls())
    summary = _summary(sandbox)
    assert summary["result"] == "fail"
    assert summary["failed_at"] == "build"


def test_dry_run_changes_nothing_and_writes_no_journal(sandbox: Sandbox) -> None:
    result = sandbox.run("--dry-run", "--skip-platform")

    assert result.returncode == 0, result.stderr
    assert "--dry-run: изменения не применяю" in result.stdout
    assert "фаза" not in result.stdout
    assert "итого" not in result.stdout
    assert sandbox.journal_lines() == []
    assert not any(
        c.startswith("docker compose") or " build" in c for c in sandbox.stub_calls()
    )
    assert _git(sandbox.prod, "rev-parse", "--short", "HEAD") == sandbox.old_commit


def test_health_timeout_default_is_still_150_seconds() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    retries = int(re.search(r'HEALTH_RETRIES="\$\{HEALTH_RETRIES:-(\d+)\}"', script)[1])  # type: ignore[index]
    delay = int(re.search(r'HEALTH_DELAY="\$\{HEALTH_DELAY:-(\d+)\}"', script)[1])  # type: ignore[index]

    assert retries * delay == 150
    assert delay <= 2

