#!/usr/bin/env bash
# Деплой hs-data-api из git: pull -> сборка образа -> перезапуск -> проверка.
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
STAMP_FILE="$INSTALL_DIR/.deployed-commit"

ALLOW_DIRTY=0
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --allow-dirty) ALLOW_DIRTY=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
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

[[ "$(id -u)" -eq 0 ]] || die "запускать через sudo"
[[ -d "$INSTALL_DIR/.git" ]] || die "$INSTALL_DIR не git-репозиторий"

cd "$INSTALL_DIR"

# Ручные правки прямо на сервере — это то, от чего мы уходим. Молча затирать
# их нельзя, поэтому деплой останавливается и показывает, что именно мешает.
if [[ -n "$(git status --porcelain)" ]]; then
  if [[ "$ALLOW_DIRTY" -eq 1 ]]; then
    say "рабочее дерево изменено, продолжаю по --allow-dirty"
  else
    git status --short
    die "рабочее дерево изменено. Перенесите правки в репозиторий или запустите с --allow-dirty"
  fi
fi

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
  say "--dry-run: изменения не применяю"
  exit 0
fi

git pull --ff-only --quiet origin "$BRANCH"
COMMIT="$(git rev-parse --short HEAD)"
ensure_host_services_can_read_source

# Тег на прежний образ, чтобы откат не зависел от повторной сборки.
if docker image inspect hs-data-api:local >/dev/null 2>&1; then
  say "прежний образ помечаю как rollback-$OLD_COMMIT"
  docker image tag hs-data-api:local "hs-data-api:rollback-$OLD_COMMIT"
fi

say "собираю образ"
docker compose -f "$COMPOSE_FILE" build api

say "перезапускаю"
docker compose -f "$COMPOSE_FILE" up -d api

say "проверяю здоровье: $HEALTH_URL"
for attempt in $(seq 1 "$HEALTH_RETRIES"); do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" || true)"
  if [[ "$code" == "200" ]]; then
    say "здоров с попытки $attempt"
    printf '%s\n' "$COMMIT" > "$STAMP_FILE"
    say "готово: в проде $COMMIT"
    exit 0
  fi
  sleep "$HEALTH_DELAY"
done

# Сюда попадаем, только если новая сборка не поднялась. Возвращаем прежний
# образ, иначе сервис останется лежать до ручного вмешательства.
echo "ОШИБКА: сервис не ответил за $((HEALTH_RETRIES * HEALTH_DELAY)) с, откатываюсь" >&2
if docker image inspect "hs-data-api:rollback-$OLD_COMMIT" >/dev/null 2>&1; then
  docker image tag "hs-data-api:rollback-$OLD_COMMIT" hs-data-api:local
  docker compose -f "$COMPOSE_FILE" up -d api
  echo "Откат на $OLD_COMMIT выполнен. Код в рабочем каталоге остался новым: $COMMIT" >&2
fi
exit 1
