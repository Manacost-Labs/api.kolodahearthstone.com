#!/usr/bin/env bash
# Деплой hs-data-api из git — единственный путь обновления прода:
#   1. pull чистого origin/<branch>;
#   2. платформа: deploy-platform.sh + apply-migrations.sh (до рестарта API);
#   3. сборка образа -> перезапуск -> /v1/health (при сбое — откат образа);
#   4. systemd-юниты Docker-задач (install-docker-systemd.sh) и проверка
#      host-side exporter расписаний.
# --dry-run показывает план каждого шага и ничего не меняет.
# --skip-platform / --skip-units пропускают шаг 2 / 4 (аварийный обход).
# Образ собирается только из чистого дерева, совпадающего с origin/<branch>,
# и получает label org.opencontainers.image.revision=<полный SHA>.
#
# Заменяет копирование файлов через deploy-local.sh. Разница принципиальная:
# после копирования сервер не помнил, какая версия на нём стоит, потому что
# .git оставался нетронутым и показывал давно устаревший коммит. Здесь версия
# в проде — это то, что показывает git, и то же значение попадает в тег образа
# и в файл-отметку.
set -euo pipefail
umask 0022

INSTALL_DIR="${INSTALL_DIR:-/srv/hs-data-api}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:18081/v1/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_DELAY="${HEALTH_DELAY:-5}"
HOST_SERVICE_USER="${HOST_SERVICE_USER:-debian}"
HOST_TIMER_EXPORT_UNIT="${HOST_TIMER_EXPORT_UNIT:-hs-data-api-docker-export-timer-state.service}"
HOST_TIMER_SNAPSHOT="$INSTALL_DIR/data/parser-control-systemd.json"
STAMP_FILE="$INSTALL_DIR/.deployed-commit"
PLATFORM_TARGET="${HS_DATA_PLATFORM_TARGET_ROOT:-/srv/hs-data-platform}"
PLATFORM_DB_CONTAINER="${PLATFORM_DB_CONTAINER:-hs-data-postgres-1}"
SYSTEMD_DIR="${SYSTEMD_DIR:-/etc/systemd/system}"
LOCK_FILE="${DEPLOY_LOCK_FILE:-/run/lock/hs-data-api-deploy.lock}"
COMPOSE_PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$INSTALL_DIR")}"

DRY_RUN=0
SKIP_PLATFORM=0
SKIP_UNITS=0
for arg in "$@"; do
  case "$arg" in
    --allow-dirty)
      echo "ОШИБКА: --allow-dirty больше не поддерживается: образ собирается только из чистого origin/$BRANCH" >&2
      exit 2 ;;
    --dry-run) DRY_RUN=1 ;;
    --skip-platform) SKIP_PLATFORM=1 ;;
    --skip-units) SKIP_UNITS=1 ;;
    -h|--help)
      sed -n '2,21p' "$0"; exit 0 ;;
    *) echo "Неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done

die() { echo "ОШИБКА: $*" >&2; exit 1; }
say() { echo "==> $*"; }

ensure_host_services_can_read_source() {
  local path

  # Deployment запускается от root, а host-side exporter — от непривилегированного
  # пользователя. Исправляем права только у публичного tracked-кода app, не
  # затрагивая .env, credentials, data и другие runtime-файлы.
  find app -type d -exec chmod a+rx {} +
  while IFS= read -r -d '' path; do
    [[ -f "$path" ]] && chmod a+r -- "$path"
  done < <(git ls-files -z -- app)

  id "$HOST_SERVICE_USER" >/dev/null 2>&1 \
    || die "пользователь host-служб $HOST_SERVICE_USER не существует"
  runuser -u "$HOST_SERVICE_USER" -- test -r "$INSTALL_DIR/app/systemd_timer_export.py" \
    || die "$HOST_SERVICE_USER не может читать код host-side exporter"
}

rollback_api() {
  if docker image inspect "hs-data-api:rollback-$OLD_COMMIT" >/dev/null 2>&1; then
    docker image tag "hs-data-api:rollback-$OLD_COMMIT" hs-data-api:local
    docker compose -f "$COMPOSE_FILE" up -d --no-deps --no-build api
    echo "Откат на $OLD_COMMIT выполнен. Код в рабочем каталоге остался новым: $COMMIT" >&2
  fi
}

verify_host_timer_export() {
  systemctl cat "$HOST_TIMER_EXPORT_UNIT" >/dev/null 2>&1 \
    || { echo "Не установлен $HOST_TIMER_EXPORT_UNIT" >&2; return 1; }
  systemctl start "$HOST_TIMER_EXPORT_UNIT" \
    || { systemctl --no-pager --full status "$HOST_TIMER_EXPORT_UNIT" >&2 || true; return 1; }
  runuser -u "$HOST_SERVICE_USER" -- test -s "$HOST_TIMER_SNAPSHOT" \
    || { echo "Exporter не создал $HOST_TIMER_SNAPSHOT" >&2; return 1; }
}

# План миграций считается раннером из origin/$BRANCH: новые файлы sql ещё не
# лежат на диске. Старый раннер не знал --dry-run и применил бы всё, поэтому
# без поддержки флага план не строится.
plan_migrations() {
  local tmp
  tmp="$(mktemp -d)"
  git archive "origin/$BRANCH" platform/scripts/apply-migrations.sh platform/sql | tar -x -C "$tmp"
  if grep -q -- '--dry-run)' "$tmp/platform/scripts/apply-migrations.sh"; then
    PLATFORM_DOCKER=docker PLATFORM_PSQL_CONTAINER="$PLATFORM_DB_CONTAINER" \
      "$tmp/platform/scripts/apply-migrations.sh" --dry-run | sed 's/^/    /' \
      || echo "    не удалось получить план миграций (контейнер $PLATFORM_DB_CONTAINER?)"
  else
    echo "    раннер в origin/$BRANCH не поддерживает --dry-run; план не построен"
  fi
  rm -rf "$tmp"
}

# unit_plan <каталог юнитов в репо> <glob> <render 0|1>: какие юниты из
# origin/$BRANCH появятся или изменятся в $SYSTEMD_DIR. render=1 повторяет
# подстановку INSTALL_DIR из install-docker-systemd.sh.
unit_plan() {
  local dir="$1" pattern="$2" render="$3" path name rendered changes=0
  while IFS= read -r path; do
    name="${path##*/}"
    # shellcheck disable=SC2254 # pattern is an intentional glob
    case "$name" in $pattern) ;; *) continue ;; esac
    rendered="$(git show "origin/$BRANCH:$path")"
    [[ "$render" -eq 1 ]] && rendered="${rendered//\/srv\/hs-data-api/$INSTALL_DIR}"
    if [[ ! -f "$SYSTEMD_DIR/$name" ]]; then
      echo "    новый: $name"; changes=$((changes + 1))
    elif [[ "$rendered" != "$(cat "$SYSTEMD_DIR/$name")" ]]; then
      echo "    изменится: $name"; changes=$((changes + 1))
    fi
  done < <(git ls-tree --name-only "origin/$BRANCH" "$dir/")
  [[ "$changes" -gt 0 ]] || echo "    без изменений"
}

# Job-контейнеры (`docker compose run` из юнитов) прежней ревизии доживают
# свой прогон на старом коде: могут перезаписать результат нового кода или
# держать lock. Деплой их не убивает (прогон может идти часами), а называет.
report_old_job_containers() {
  local revision="$1" rows
  rows="$(docker ps \
    --filter label=com.docker.compose.oneoff=True \
    --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" \
    --format '{{.Names}}|{{.Label "org.opencontainers.image.revision"}}|{{.RunningFor}}' \
    | awk -F'|' -v rev="$revision" '$2 != rev {printf "    %s (ревизия %.7s, запущен %s)\n", $1, $2, $3}' \
    || true)"
  [[ -n "$rows" ]] || return 0
  echo "ВНИМАНИЕ: job-контейнеры работают не на ревизии ${revision:0:7}:" >&2
  echo "$rows" >&2
  echo "    Дождитесь окончания или остановите: sudo systemctl stop <имя контейнера>.service" >&2
  echo "    (контейнер без имени юнита: docker stop <имя>)" >&2
}

[[ "$(id -u)" -eq 0 ]] || die "запускать через sudo"
[[ -d "$INSTALL_DIR/.git" ]] || die "$INSTALL_DIR не git-репозиторий"

cd "$INSTALL_DIR"

exec 9>"$LOCK_FILE"
flock -n 9 || die "другой деплой уже идёт ($LOCK_FILE)"

# Ручные правки прямо на сервере — это то, от чего мы уходим. Молча затирать
# их нельзя, а собирать из них образ — значит снова получить prod, которого нет
# ни в одном коммите. Поэтому деплой останавливается и показывает, что мешает.
require_clean_tree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    git status --short
    die "рабочее дерево изменено. Перенесите правки в репозиторий (patch/ветка) и повторите"
  fi
}

require_clean_tree

OLD_COMMIT="$(git rev-parse --short HEAD)"
say "текущий коммит: $OLD_COMMIT"

say "получаю изменения из origin/$BRANCH"
git fetch --quiet origin "$BRANCH"
NEW_COMMIT="$(git rev-parse --short "origin/$BRANCH")"

if [[ "$OLD_COMMIT" == "$NEW_COMMIT" ]]; then
  say "уже на origin/$BRANCH ($NEW_COMMIT)"
else
  say "приедет: $OLD_COMMIT -> $NEW_COMMIT"
  git --no-pager log --oneline "HEAD..origin/$BRANCH" | sed 's/^/    /'
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  if [[ "$SKIP_PLATFORM" -eq 1 ]]; then
    say "платформа: пропущу (--skip-platform)"
  else
    say "платформа: синхронизирую в $PLATFORM_TARGET; изменения в platform/:"
    git --no-pager diff --stat "HEAD..origin/$BRANCH" -- platform | sed 's/^/    /'
    say "платформа: миграции"
    plan_migrations
    say "платформа: systemd-юниты"
    unit_plan platform/systemd '*' 0
  fi
  say "API: соберу образ из origin/$BRANCH, перезапущу, проверю $HEALTH_URL"
  report_old_job_containers "$(git rev-parse "origin/$BRANCH")"
  if [[ "$SKIP_UNITS" -eq 1 ]]; then
    say "юниты Docker-задач: пропущу (--skip-units)"
  else
    say "юниты Docker-задач"
    unit_plan systemd 'hs-data-api-docker*' 1
  fi
  say "--dry-run: изменения не применяю"
  exit 0
fi

git pull --ff-only --quiet origin "$BRANCH"
COMMIT="$(git rev-parse --short HEAD)"
REVISION="$(git rev-parse HEAD)"
# Сборка только из дерева, которое ровно равно origin/$BRANCH: не отстаёт,
# не опережает локальными коммитами и не содержит правок.
[[ "$REVISION" == "$(git rev-parse "origin/$BRANCH")" ]] \
  || die "HEAD $COMMIT не совпадает с origin/$BRANCH; сборка отменена"
require_clean_tree
ensure_host_services_can_read_source

# Миграции идут до рестарта: новый код API может ждать новые таблицы. При
# сбое прежний контейнер продолжает работать, код на диске уже новый.
if [[ "$SKIP_PLATFORM" -eq 1 ]]; then
  say "платформа пропущена (--skip-platform)"
else
  say "платформа: синхронизирую $PLATFORM_TARGET"
  HS_DATA_PLATFORM_TARGET_ROOT="$PLATFORM_TARGET" scripts/deploy-platform.sh \
    || die "deploy-platform.sh завершился ошибкой; API не перезапущен"
  say "платформа: миграции"
  "$PLATFORM_TARGET/scripts/apply-migrations.sh" \
    || die "миграции не применились; API не перезапущен (старый контейнер работает)"
fi

# Тег на прежний образ, чтобы откат не зависел от повторной сборки.
if docker image inspect hs-data-api:local >/dev/null 2>&1; then
  say "прежний образ помечаю как rollback-$OLD_COMMIT"
  docker image tag hs-data-api:local "hs-data-api:rollback-$OLD_COMMIT"
fi

say "собираю образ из $REVISION"
HS_IMAGE_REVISION="$REVISION" docker compose -f "$COMPOSE_FILE" build api
built_revision="$(docker image inspect hs-data-api:local \
  --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}')"
if [[ "$built_revision" != "$REVISION" ]]; then
  # Тег :local не должен указывать на непроверенный образ: следующий
  # `docker compose up` запустил бы его.
  if docker image inspect "hs-data-api:rollback-$OLD_COMMIT" >/dev/null 2>&1; then
    docker image tag "hs-data-api:rollback-$OLD_COMMIT" hs-data-api:local
  fi
  die "у собранного образа revision=$built_revision вместо $REVISION; контейнер не перезапущен"
fi

say "перезапускаю"
docker compose -f "$COMPOSE_FILE" up -d --no-build api

say "проверяю здоровье: $HEALTH_URL"
API_HEALTHY=0
for attempt in $(seq 1 "$HEALTH_RETRIES"); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" || true)"
  if [[ "$code" == "200" ]]; then
    say "здоров с попытки $attempt"
    API_HEALTHY=1
    break
  fi
  sleep "$HEALTH_DELAY"
done

if [[ "$API_HEALTHY" -ne 1 ]]; then
  echo "ОШИБКА: сервис не ответил за $((HEALTH_RETRIES * HEALTH_DELAY)) с, откатываюсь" >&2
  rollback_api
  exit 1
fi

# Юниты ставятся после здорового API: новые задачи вызывают команды нового
# образа. Exporter ниже проверяется уже с новыми юнитами.
if [[ "$SKIP_UNITS" -eq 1 ]]; then
  say "юниты Docker-задач пропущены (--skip-units)"
else
  say "устанавливаю юниты Docker-задач"
  if ! INSTALL_DIR="$INSTALL_DIR" SYSTEMD_DIR="$SYSTEMD_DIR" scripts/install-docker-systemd.sh; then
    echo "ОШИБКА: install-docker-systemd.sh не прошёл; API уже обновлён до $COMMIT, отметка не записана" >&2
    exit 1
  fi
fi

say "проверяю host-side exporter расписаний"
if ! verify_host_timer_export; then
  echo "ОШИБКА: exporter расписаний не прошёл post-deploy проверку, откатываюсь" >&2
  rollback_api
  exit 1
fi

report_old_job_containers "$REVISION"
printf '%s\n' "$COMMIT" > "$STAMP_FILE"
say "готово: в проде $COMMIT"
