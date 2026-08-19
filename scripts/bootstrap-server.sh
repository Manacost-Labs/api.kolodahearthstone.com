#!/usr/bin/env bash
# Развёртывание hs-data-api на чистом сервере: зависимости, клон, образ,
# systemd-юниты, проверка. Повторный запуск безопасен и работает как
# обновление.
#
#   curl -fsSL https://raw.githubusercontent.com/Manacost-Labs/api.kolodahearthstone.com/main/scripts/bootstrap-server.sh | sudo bash
#
# Флаги:
#   --check      только проверить готовность окружения, ничего не менять
#   --no-units   пропустить установку systemd-таймеров
#   --no-start   собрать образ, но не поднимать контейнер
set -euo pipefail

REPO_URL="${HS_REPO_URL:-https://github.com/Manacost-Labs/api.kolodahearthstone.com.git}"
INSTALL_DIR="${INSTALL_DIR:-/srv/hs-data-api}"
BRANCH="${BRANCH:-main}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
ENV_FILE_NAME="${ENV_FILE_NAME:-.env.docker}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:18081/v1/health}"
HEALTH_RETRIES="${HEALTH_RETRIES:-30}"
HEALTH_DELAY="${HEALTH_DELAY:-5}"

CHECK_ONLY=0
WITH_UNITS=1
DO_START=1
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
    --no-units) WITH_UNITS=0 ;;
    --no-start) DO_START=0 ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "Неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done

die() { echo "ОШИБКА: $*" >&2; exit 1; }
say() { echo "==> $*"; }
warn() { echo "    ! $*" >&2; }

[[ "$(id -u)" -eq 0 ]] || die "запускать через sudo"

# ---- 1. Зависимости --------------------------------------------------------
say "проверяю зависимости"
missing=()
for bin in git curl docker; do
  command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
if ! docker compose version >/dev/null 2>&1; then
  missing+=("docker-compose-plugin")
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  if [[ "$CHECK_ONLY" -eq 1 ]]; then
    die "не хватает: ${missing[*]}"
  fi
  if command -v apt-get >/dev/null 2>&1; then
    say "ставлю: ${missing[*]}"
    apt-get update -qq
    for pkg in "${missing[@]}"; do
      case "$pkg" in
        docker|docker-compose-plugin)
          # Docker из репозитория дистрибутива бывает без compose v2,
          # поэтому берём официальный установщик.
          if ! command -v docker >/dev/null 2>&1; then
            curl -fsSL https://get.docker.com | sh
          fi
          ;;
        *) apt-get install -y -qq "$pkg" ;;
      esac
    done
  else
    die "нет apt-get, поставьте вручную: ${missing[*]}"
  fi
fi
docker compose version >/dev/null 2>&1 || die "docker compose v2 недоступен"
say "зависимости на месте"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  say "--check: окружение готово, ничего не менял"
  exit 0
fi

# ---- 2. Исходники ----------------------------------------------------------
if [[ -d "$INSTALL_DIR/.git" ]]; then
  say "репозиторий уже есть, обновляю"
  git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
  git -C "$INSTALL_DIR" fetch --quiet origin "$BRANCH"
  if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain)" ]]; then
    warn "рабочее дерево изменено, оставляю как есть"
    warn "для обновления используйте scripts/deploy-server.sh"
  else
    git -C "$INSTALL_DIR" pull --ff-only --quiet origin "$BRANCH"
  fi
else
  [[ -e "$INSTALL_DIR" ]] && die "$INSTALL_DIR существует и не является git-репозиторием"
  say "клонирую в $INSTALL_DIR"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi
cd "$INSTALL_DIR"
say "коммит: $(git rev-parse --short HEAD)"

# ---- 3. Переменные окружения ----------------------------------------------
# Секреты в репозиторий не входят, поэтому на чистом сервере файл создаётся из
# шаблона и установка останавливается: без ключей сервис всё равно бесполезен.
FRESH_ENV=0
if [[ ! -f "$ENV_FILE_NAME" ]]; then
  cp .env.example "$ENV_FILE_NAME"
  chmod 600 "$ENV_FILE_NAME"
  FRESH_ENV=1
  say "создал $ENV_FILE_NAME из .env.example"
fi

# ---- 4. Образ и контейнер --------------------------------------------------
# Тег образа задан в compose жёстко, поэтому сборка из любого каталога
# перевешивает общий hs-data-api:local. На машине с боевой установкой это
# незаметно для работающего контейнера (он держит образ по ID), но при
# следующем перезапуске поднимется чужая сборка.
if [[ "$INSTALL_DIR" != "/srv/hs-data-api" && -d /srv/hs-data-api/.git ]]; then
  warn "на этой машине есть боевая установка в /srv/hs-data-api"
  warn "сборка перевесит тег hs-data-api:local на образ из $INSTALL_DIR"
  warn "после проверки соберите боевой заново: sudo /srv/hs-data-api/scripts/deploy-server.sh"
fi

say "собираю образ"
docker compose -f "$COMPOSE_FILE" build api

if [[ "$FRESH_ENV" -eq 1 ]]; then
  echo
  say "образ собран, но контейнер не поднимаю"
  echo "    Заполните секреты в $INSTALL_DIR/$ENV_FILE_NAME и запустите:"
  echo "      sudo $INSTALL_DIR/scripts/deploy-server.sh"
  exit 0
fi

if [[ "$DO_START" -eq 0 ]]; then
  say "--no-start: контейнер не поднимаю"
else
  say "поднимаю контейнер"
  docker compose -f "$COMPOSE_FILE" up -d api
fi

# ---- 5. Расписания ---------------------------------------------------------
if [[ "$WITH_UNITS" -eq 1 ]]; then
  if command -v systemctl >/dev/null 2>&1; then
    say "ставлю systemd-юниты"
    INSTALL_DIR="$INSTALL_DIR" ./scripts/install-docker-systemd.sh
  else
    warn "systemd недоступен, расписания пропущены"
  fi
fi

# ---- 6. Проверка -----------------------------------------------------------
if [[ "$DO_START" -eq 1 ]]; then
  say "проверяю здоровье: $HEALTH_URL"
  for attempt in $(seq 1 "$HEALTH_RETRIES"); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$HEALTH_URL" || true)"
    if [[ "$code" == "200" ]]; then
      say "здоров с попытки $attempt"
      printf '%s\n' "$(git rev-parse --short HEAD)" > "$INSTALL_DIR/.deployed-commit"
      echo
      say "готово. Дальнейшие обновления: sudo $INSTALL_DIR/scripts/deploy-server.sh"
      exit 0
    fi
    sleep "$HEALTH_DELAY"
  done
  die "сервис не ответил за $((HEALTH_RETRIES * HEALTH_DELAY)) с. Логи: docker compose -f $COMPOSE_FILE logs api"
fi

say "готово"
