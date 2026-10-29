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

CLOCK_START_MS = 1_800_000_000_000

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
if [[ "${STUB_CURL_HANGS:-0}" == 1 ]]; then
  # Зависший сервис: ждём ровно до --max-time и отвечаем «нет ответа» (000).
  while [[ $# -gt 0 ]]; do
    [[ "$1" == --max-time ]] && { sleep "$2"; printf '000'; exit 28; }
    shift
  done
fi
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
    # Фиктивные часы (мокается только время, §8 п.1): общий файл $STUB_CLOCK
    # хранит «текущие» мс. sleep и зависший curl сдвигают его, не ждя на самом
    # деле; date отдаёт его в формате `+%s%3N`, который использует скрипт.
    "date": r"""#!/usr/bin/env bash
if [[ "$*" == "+%s%3N" ]]; then cat "$STUB_CLOCK"; exit 0; fi
exec /usr/bin/date "$@"
""",
    "sleep": r"""#!/usr/bin/env bash
awk -v now="$(cat "$STUB_CLOCK")" -v s="$1" 'BEGIN { printf "%d", now + s * 1000 }' \
  > "$STUB_CLOCK.tmp" && mv "$STUB_CLOCK.tmp" "$STUB_CLOCK"
""",
    "logger": r"""#!/usr/bin/env bash
echo "$*" >> "$STUB_JOURNAL"
""",
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

    @property
    def clock(self) -> Path:
        return self.root / "clock_ms"

    def elapsed_seconds(self) -> float:
        """Фиктивное время, прошедшее с начала запуска."""
        return (int(self.clock.read_text()) - CLOCK_START_MS) / 1000

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        self.clock.write_text(str(CLOCK_START_MS))
        full_env = {
            "PATH": f"{self.root / 'bin'}:/usr/bin:/bin",
            "HOME": str(self.root),
            "INSTALL_DIR": str(self.prod),
            "DEPLOY_LOCK_FILE": str(self.root / "deploy.lock"),
            "SYSTEMD_DIR": str(self.root / "systemd"),
            "STUB_CLOCK": str(self.clock),
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
    # Попытки шли весь бюджет (75 × 2 с), до юнитов дело не дошло.
    assert sum(c.startswith("curl ") for c in calls) >= 70
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


@pytest.mark.parametrize("curl_hangs", ["0", "1"], ids=["http-503", "hangs"])
def test_default_health_budget_is_150_seconds_whatever_the_endpoint_does(
    sandbox: Sandbox, curl_hangs: str
) -> None:
    # Ни одной HEALTH_*: проверяем дефолты. Часы фиктивные, поэтому тест
    # мгновенный. Бюджет по времени: ≥150 с и не растягивается зависшими
    # запросами (по числу попыток 75 × (10 + 2) с = 900 с).
    result = sandbox.run(
        "--skip-platform", STUB_HEALTH_CODE="503", STUB_CURL_HANGS=curl_hangs
    )

    assert result.returncode == 1
    assert "не ответил за 150 с" in result.stderr
    assert 150 <= sandbox.elapsed_seconds() < 160
    assert (
        f"docker image tag hs-data-api:rollback-{sandbox.old_commit} hs-data-api:local"
        in sandbox.stub_calls()
    )
    summary = _summary(sandbox)
    assert summary["result"] == "rollback"
    assert summary["failed_at"] == "health"
    assert 150 <= float(summary["health"].rstrip("s")) < 160


def test_hanging_health_endpoint_rolls_back_within_the_time_budget(
    sandbox: Sandbox,
) -> None:
    # Бюджет 2 × 1 с = 2 с. Цикл «по числу попыток» ждал бы
    # 2 × (max-time 10 с + пауза 1 с) = 22 с.
    result = sandbox.run(
        "--skip-platform", STUB_CURL_HANGS="1", HEALTH_RETRIES="2", HEALTH_DELAY="1"
    )

    assert result.returncode == 1
    assert 2 <= sandbox.elapsed_seconds() < 3
    assert "не ответил за 2 с" in result.stderr
    curls = [c for c in sandbox.stub_calls() if c.startswith("curl ")]
    assert curls
    for call in curls:
        max_time = float(call.split("--max-time ")[1].split()[0])
        assert 0 < max_time <= 2.0
    summary = _summary(sandbox)
    assert summary["result"] == "rollback"
    assert summary["failed_at"] == "health"


def test_explicit_health_timeout_overrides_retries_times_delay(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run(
        "--skip-platform",
        STUB_CURL_HANGS="1",
        HEALTH_TIMEOUT="1",
        HEALTH_RETRIES="75",
        HEALTH_DELAY="2",
    )

    assert result.returncode == 1
    assert 1 <= sandbox.elapsed_seconds() < 2
    assert "не ответил за 1 с" in result.stderr
